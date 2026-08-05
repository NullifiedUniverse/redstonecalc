/* The redstone engine, in JavaScript.

   One copy, injected into both pages by `tools/build_pages.py`. It used to live
   inside `docs/demo_template.html`, and the preview page was built by slicing it
   back out with `index()` and `rindex()` on two marker strings — which meant the
   older Mk I demo page was also a *source file* for the newer one, and could not
   be edited without risk of silently changing what the Mk III page runs on.

   The rules here are the same rules `rscalc/engine.py` implements, and
   `tests/test_minecraft_rules.py` runs the two side by side, every block at
   every tick. `tests/test_export.py` checks that the tables below still agree
   with the Python ones, because a kind byte is only an index into this list.
*/

/* The kind table: the exported blob's own ordering, written out in full. The
   code indexes blocks with the K_* constants below, so this looks unused —
   it is not. The preview page names an ordinary block from it rather than
   keeping a second list that can disagree, and `tools/build_pages.py` finds the
   start of this engine by looking for its first line. */
const KINDS=["solid","redstone_wire","redstone_torch","repeater","comparator",
             "lever","lamp","redstone_block","glass"];
const K_SOLID=0,K_WIRE=1,K_TORCH=2,K_REP=3,K_CMP=4,K_LEVER=5,K_LAMP=6,K_RBLOCK=7,K_GLASS=8;
const DIRV=[[0,0,-1],[0,0,1],[-1,0,0],[1,0,0],[0,1,0],[0,-1,0]]; // n s w e u d
const OPP=[1,0,3,2,5,4];
const HORIZ=[0,1,2,3];
const AXIS=[1,1,0,0,2,2];  // z z x x y y
const CONDUCT=k=>k===K_SOLID||k===K_LAMP;

/* The tick queue is a binary min-heap on the scheduled time, kept in a plain
   array so `q[0]` is still the next event and `q.length=0` still clears it.
   Sorting the whole queue on every schedule() is what made a 36k-block world
   unusable in a browser; this is the same order, at a hundredth of the cost. */
function qpush(q,it){
  q.push(it);
  let i=q.length-1;
  while(i>0){const p=(i-1)>>1; if(q[p][0]<=q[i][0])break;
    const t=q[p];q[p]=q[i];q[i]=t;i=p;}
}
function qpop(q){
  const top=q[0],last=q.pop();
  if(q.length){
    q[0]=last;
    let i=0;
    for(;;){const l=2*i+1,r=l+1;let m=i;
      if(l<q.length&&q[l][0]<q[m][0])m=l;
      if(r<q.length&&q[r][0]<q[m][0])m=r;
      if(m===i)break;
      const t=q[m];q[m]=q[i];q[i]=t;i=m;}
  }
  return top;
}

async function inflate(b64){
  const bin=Uint8Array.from(atob(b64),c=>c.charCodeAt(0));
  const ds=new DecompressionStream("gzip");
  const buf=await new Response(new Blob([bin]).stream().pipeThrough(ds)).arrayBuffer();
  return new Uint8Array(buf);
}

class World{
  constructor(n,dims,raw,state){
    this.n=n; this.dims=dims;
    const [DX,DY,DZ]=dims;
    this.DY=DY; this.DZ=DZ;
    this.bx=new Int16Array(n); this.by=new Int16Array(n); this.bz=new Int16Array(n);
    this.kind=new Uint8Array(n); this.meta=new Uint8Array(n);
    this.power=new Uint8Array(n);   // dust strength / comparator out / lamp
    this.lit=new Uint8Array(n);     // torch lit, repeater powered, lever on
    const dv=new DataView(raw.buffer,raw.byteOffset,raw.byteLength);
    for(let i=0;i<n;i++){
      this.bx[i]=dv.getUint16(i*2,true);
      this.by[i]=dv.getUint16(n*2+i*2,true);
      this.bz[i]=dv.getUint16(n*4+i*2,true);
    }
    this.kind.set(raw.subarray(n*6,n*7));
    this.meta.set(raw.subarray(n*7,n*8));
    this.map=new Map();
    for(let i=0;i<n;i++) this.map.set(this.key(this.bx[i],this.by[i],this.bz[i]),i);
    if(state){
      // a settled state computed offline: relaxing a large world in the page
      // would cost minutes to arrive at an answer that is already known
      this.power.set(state.subarray(0,n));
      for(let i=0;i<n;i++) this.lit[i]=state[n+i];
    } else {
      for(let i=0;i<n;i++){
        if(this.kind[i]===K_TORCH) this.lit[i]=1;
        if(this.kind[i]===K_LEVER) this.lit[i]=(this.meta[i]>>3)&1;
      }
    }
  }
  key(x,y,z){return (x*this.DY+y)*this.DZ+z;}
  at(x,y,z){
    if(x<0||y<0||z<0||x>=this.dims[0]||y>=this.dims[1]||z>=this.dims[2])return -1;
    const v=this.map.get(this.key(x,y,z)); return v===undefined?-1:v;
  }
  step(i,d){const v=DIRV[d];return this.at(this.bx[i]+v[0],this.by[i]+v[1],this.bz[i]+v[2]);}
  kindAt(i){return i<0?-1:this.kind[i];}
  facing(i){return this.meta[i]&7;}
  delay(i){return (this.meta[i]>>3)+1;}
  attach(i){return this.meta[i]&7;}
}

class Engine{
  constructor(w){
    this.w=w; this.now=0; this.queue=[]; this.pending=new Map(); this.burned=new Set();
    // when non-null, every block whose state changes is recorded here, so the
    // renderer can repaint a wavefront instead of half a million instances
    this.touched=null;
    this.compile();
  }
  /* ---- static topology ---- */
  compile(){
    const w=this.w,n=w.n;
    this.links=new Map();          // dust index -> [dust indices]
    this.points=new Uint8Array(n); // 4-bit mask of horizontal directions
    for(let i=0;i<n;i++){
      if(w.kind[i]!==K_WIRE) continue;
      const conns=[],targets=[];
      for(const d of HORIZ){
        const t=this.connectTarget(i,d);
        if(t>=0){conns.push(d);targets.push(t);}
      }
      let mask=0;
      if(conns.length===1) mask=(1<<conns[0])|(1<<OPP[conns[0]]);
      else if(conns.length>1) for(const d of conns) mask|=1<<d;
      this.points[i]=mask;
      this.links.set(i,targets.filter(t=>w.kind[t]===K_WIRE));
    }
    // connected components of dust
    this.netOf=new Int32Array(n).fill(-1); this.nets=[];
    for(let i=0;i<n;i++){
      if(w.kind[i]!==K_WIRE||this.netOf[i]>=0) continue;
      const id=this.nets.length,mem=[],stack=[i];
      this.netOf[i]=id;
      while(stack.length){
        const c=stack.pop(); mem.push(c);
        for(const t of this.links.get(c)) if(this.netOf[t]<0){this.netOf[t]=id;stack.push(t);}
      }
      this.nets.push(mem);
    }
    // strong power sources per block
    this.strong=new Map();
    const push=(m,k,v)=>{const a=m.get(k);a?a.push(v):m.set(k,[v]);};
    for(let i=0;i<n;i++){
      const k=w.kind[i];
      if(k===K_TORCH){const t=w.step(i,4); if(t>=0)push(this.strong,t,i);}
      else if(k===K_REP||k===K_CMP){const t=w.step(i,w.facing(i)); if(t>=0)push(this.strong,t,i);}
      else if(k===K_LEVER){const t=w.step(i,w.attach(i)); if(t>=0)push(this.strong,t,i);}
    }
    this.comps=[];
    for(let i=0;i<n;i++){const k=w.kind[i];
      if(k===K_TORCH||k===K_REP||k===K_CMP||k===K_LAMP) this.comps.push(i);}
    // net sources and reverse maps
    this.netSrc=new Map(); this.netsBy=new Map();
    for(let id=0;id<this.nets.length;id++){
      const src=[];
      for(const dp of this.nets[id]) for(let d=0;d<6;d++){
        const nb=w.step(dp,d); if(nb<0) continue;
        const k=w.kind[nb];
        if(k===K_TORCH||k===K_REP||k===K_CMP||k===K_LEVER||k===K_RBLOCK){
          src.push([dp,d]); push(this.netsBy,nb,id);
        } else if(CONDUCT(k)){
          src.push([dp,d]);
          for(const s of (this.strong.get(nb)||[])) push(this.netsBy,s,id);
        }
      }
      this.netSrc.set(id,src);
    }
    this.byNet=new Map(); this.byComp=new Map();
    for(const c of this.comps){
      for(const p of this.inputsOf(c)){
        if(p<0) continue;
        const k=w.kind[p];
        if(k===K_WIRE) push(this.byNet,this.netOf[p],c);
        else if(k===K_TORCH||k===K_REP||k===K_CMP||k===K_LEVER||k===K_RBLOCK) push(this.byComp,p,c);
        else if(CONDUCT(k)){
          for(const s of (this.strong.get(p)||[])) push(this.byComp,s,c);
          // dust that weakly powers this block
          const a=w.step(p,4);
          if(a>=0&&w.kind[a]===K_WIRE) push(this.byNet,this.netOf[a],c);
          for(const d of HORIZ){
            const nb=w.step(p,d);
            if(nb>=0&&w.kind[nb]===K_WIRE&&(this.points[nb]>>OPP[d]&1)) push(this.byNet,this.netOf[nb],c);
          }
        }
      }
    }
  }
  connectTarget(i,d){
    const w=this.w,nb=w.step(i,d);
    if(nb<0){
      const bx=w.bx[i]+DIRV[d][0],bz=w.bz[i]+DIRV[d][2];
      const dn=w.at(bx,w.by[i]-1,bz);
      return (dn>=0&&w.kind[dn]===K_WIRE)?dn:-1;
    }
    const k=w.kind[nb];
    if(k===K_WIRE) return nb;
    if(k===K_REP||k===K_CMP) return AXIS[w.facing(nb)]===AXIS[d]?nb:-1;
    if(k===K_TORCH||k===K_LEVER||k===K_RBLOCK) return nb;
    if(CONDUCT(k)){
      const up=w.at(w.bx[nb],w.by[nb]+1,w.bz[nb]);
      const above=w.at(w.bx[i],w.by[i]+1,w.bz[i]);
      if(up>=0&&w.kind[up]===K_WIRE&&!(above>=0&&CONDUCT(w.kind[above]))) return up;
      return -1;
    }
    const dn=w.at(w.bx[nb],w.by[nb]-1,w.bz[nb]);
    return (dn>=0&&w.kind[dn]===K_WIRE)?dn:-1;
  }
  /* ---- power ---- */
  strongPower(b){
    const w=this.w; let p=0;
    for(const s of (this.strong.get(b)||[])){
      const k=w.kind[s];
      if(k===K_TORCH){if(w.lit[s])p=15;}
      else if(k===K_REP){if(w.lit[s])p=15;}
      else if(k===K_CMP){p=Math.max(p,w.power[s]);}
      else if(k===K_LEVER){if(w.lit[s])p=15;}
      if(p===15)break;
    }
    return p;
  }
  weakPower(b){
    const w=this.w; let p=0;
    const a=w.step(b,4);
    if(a>=0&&w.kind[a]===K_WIRE) p=Math.max(p,w.power[a]);
    for(const d of HORIZ){
      const nb=w.step(b,d);
      if(nb>=0&&w.kind[nb]===K_WIRE&&w.power[nb]>0&&(this.points[nb]>>OPP[d]&1))
        p=Math.max(p,w.power[nb]);
    }
    return p;
  }
  blockPower(b){return Math.max(this.strongPower(b),this.weakPower(b));}
  dustSource(dp,d){
    const w=this.w,nb=w.step(dp,d);
    if(nb<0)return 0;
    const k=w.kind[nb];
    if(k===K_TORCH)return w.lit[nb]?15:0;
    if(k===K_LEVER)return w.lit[nb]?15:0;
    if(k===K_RBLOCK)return 15;
    if(k===K_REP)return (w.lit[nb]&&w.step(nb,w.facing(nb))===dp)?15:0;
    if(k===K_CMP)return w.step(nb,w.facing(nb))===dp?w.power[nb]:0;
    if(CONDUCT(k))return this.strongPower(nb);
    return 0;
  }
  recomputeNet(id){
    const w=this.w,mem=this.nets[id],best=new Map();
    for(const [dp,d] of this.netSrc.get(id)){
      const v=this.dustSource(dp,d);
      if(v>(best.get(dp)||0)) best.set(dp,v);
    }
    const heap=[...best.entries()].sort((a,b)=>b[1]-a[1]);
    while(heap.length){
      const [dp,v]=heap.shift();
      if(v<(best.get(dp)||0)||v<=1) continue;
      for(const nx of this.links.get(dp)){
        if(v-1>(best.get(nx)||0)){best.set(nx,v-1);
          let j=0; while(j<heap.length&&heap[j][1]>v-1)j++;
          heap.splice(j,0,[nx,v-1]);}
      }
    }
    let changed=false;
    for(const dp of mem){const v=best.get(dp)||0;
      if(w.power[dp]!==v){w.power[dp]=v;changed=true;if(this.touched)this.touched.add(dp);}}
    return changed;
  }
  inputsOf(c){
    const w=this.w,k=w.kind[c];
    if(k===K_TORCH) return [w.step(c,w.attach(c))];
    if(k===K_REP||k===K_CMP){
      const f=w.facing(c),out=[w.step(c,OPP[f])];
      for(const d of HORIZ) if(AXIS[d]!==AXIS[f]) out.push(w.step(c,d));
      return out;
    }
    if(k===K_LAMP) return [0,1,2,3,4,5].map(d=>w.step(c,d));
    return [];
  }
  // `into` matters: a repeater or comparator only emits from its front, so one
  // sitting behind another but facing away feeds it nothing.
  inputPower(p,into){
    if(p<0)return 0;
    const w=this.w,k=w.kind[p];
    if(k===K_WIRE)return w.power[p];
    if(k===K_TORCH)return w.lit[p]?15:0;
    if(k===K_LEVER)return w.lit[p]?15:0;
    if(k===K_RBLOCK)return 15;
    if(k===K_REP)return (w.lit[p]&&w.step(p,w.facing(p))===into)?15:0;
    if(k===K_CMP)return w.step(p,w.facing(p))===into?w.power[p]:0;
    if(CONDUCT(k))return this.blockPower(p);
    return 0;
  }
  sidePower(c,d){
    const w=this.w,nb=w.step(c,d);
    if(nb<0)return 0;
    const k=w.kind[nb];
    if(k===K_WIRE)return w.power[nb];
    if(k===K_RBLOCK)return 15;
    if(k===K_TORCH)return w.lit[nb]?15:0;
    if(k===K_REP)return (w.lit[nb]&&w.step(nb,w.facing(nb))===c)?15:0;
    if(k===K_CMP)return w.step(nb,w.facing(nb))===c?w.power[nb]:0;
    return 0;
  }
  desired(c){
    const w=this.w,k=w.kind[c];
    if(k===K_TORCH) return this.blockPower(w.step(c,w.attach(c)))===0?1:0;
    if(k===K_REP)   return this.inputPower(w.step(c,OPP[w.facing(c)]),c)>0?1:0;
    if(k===K_CMP){
      const f=w.facing(c),rear=this.inputPower(w.step(c,OPP[f]),c);
      let side=0;
      for(const d of HORIZ) if(AXIS[d]!==AXIS[f]) side=Math.max(side,this.sidePower(c,d));
      return ((w.meta[c]>>3)&1)?Math.max(rear-side,0):(side<=rear?rear:0);
    }
    if(k===K_LAMP){
      for(let d=0;d<6;d++){
        const nb=w.step(c,d); if(nb<0)continue;
        const kk=w.kind[nb];
        if(kk===K_WIRE&&w.power[nb]>0&&(d===4||(d<4&&(this.points[nb]>>OPP[d]&1))))return 1;
        if(kk===K_TORCH&&w.lit[nb])return 1;
        if(kk===K_LEVER&&w.lit[nb])return 1;
        if(kk===K_RBLOCK)return 1;
        if(kk===K_REP&&w.lit[nb]&&w.step(nb,w.facing(nb))===c)return 1;
        if(CONDUCT(kk)&&this.blockPower(nb)>0)return 1;
      }
      return 0;
    }
    return 0;
  }
  current(c){
    const w=this.w,k=w.kind[c];
    if(k===K_CMP)return w.power[c];
    if(k===K_LAMP)return w.power[c]>0?1:0;
    return w.lit[c];
  }
  apply(c,v){
    const w=this.w,k=w.kind[c];
    if(k===K_CMP)w.power[c]=v;
    else if(k===K_LAMP)w.power[c]=v?15:0;
    else w.lit[c]=v;
    if(this.touched)this.touched.add(c);
  }
  delayOf(c){
    const w=this.w,k=w.kind[c];
    if(k===K_TORCH)return 2;
    if(k===K_REP)return 2*w.delay(c);
    if(k===K_CMP)return 2;
    return 0;
  }
  locked(c){
    const w=this.w,f=w.facing(c);
    for(const d of HORIZ){
      if(AXIS[d]===AXIS[f])continue;
      const nb=w.step(c,d); if(nb<0)continue;
      if(w.kind[nb]===K_REP&&w.lit[nb]&&w.step(nb,w.facing(nb))===c)return true;
      if(w.kind[nb]===K_CMP&&w.power[nb]>0&&w.step(nb,w.facing(nb))===c)return true;
    }
    return false;
  }
  schedule(c,t){
    const cur=this.pending.has(c)?this.pending.get(c):this.current(c);
    if(cur===t)return;
    const d=this.delayOf(c);
    if(d===0){this.apply(c,t);this.dirtyFrom(c);return;}
    this.pending.set(c,t);
    qpush(this.queue,[this.now+d,c,t]);
  }
  dirtyFrom(c){
    for(const n of (this.netsBy.get(c)||[])) this.dn.add(n);
    for(const k of (this.byComp.get(c)||[])) this.dc.add(k);
  }
  settle(changed){
    this.dn=new Set(); this.dc=new Set();
    const full=!changed;
    if(full){for(let i=0;i<this.nets.length;i++)this.dn.add(i); for(const c of this.comps)this.dc.add(c);}
    else for(const c of changed) this.dirtyFrom(c);
    let guard=0;
    while(this.dn.size||this.dc.size){
      if(++guard>20000) throw new Error("failed to settle");
      // Drain the dust to a fixed point before asking any component what it
      // wants: interleaving the two invents glitches off intermediate dust
      // levels the tick never had. Matches rscalc/engine.py exactly.
      while(this.dn.size){
        const nets=this.dn; this.dn=new Set();
        for(const id of nets){
          const ch=this.recomputeNet(id);
          if(ch||full) for(const c of (this.byNet.get(id)||[])) this.dc.add(c);
        }
      }
      const cs=this.dc; this.dc=new Set();
      for(const c of cs){
        if(this.w.kind[c]===K_REP&&this.locked(c))continue;
        if(this.w.kind[c]===K_TORCH&&this.burned.has(c))continue;
        this.schedule(c,this.desired(c));
      }
    }
  }
  adoptState(){
    // the state on the blocks is already the resting state; just clear the
    // scheduler so timing starts from zero
    this.queue.length=0; this.pending.clear(); this.burned.clear();
    this.toggles=new Map(); this.now=0;
  }
  initializeSteady(){
    for(let it=0;it<4000;it++){
      let changed=false;
      for(let i=0;i<this.nets.length;i++) if(this.recomputeNet(i)) changed=true;
      for(const c of this.comps){
        if(this.w.kind[c]===K_REP&&this.locked(c))continue;
        const want=this.desired(c);
        if(this.current(c)!==want){this.apply(c,want);changed=true;}
      }
      if(!changed)break;
    }
    this.queue.length=0; this.pending.clear(); this.burned.clear();
    this.toggles=new Map(); this.now=0;
  }
  tick(){
    this.now++;
    const fired=[];
    while(this.queue.length&&this.queue[0][0]<=this.now){
      const [,c,t]=qpop(this.queue);
      if(this.pending.get(c)!==t)continue;
      this.pending.delete(c);
      if(this.current(c)===t)continue;
      if(this.w.kind[c]===K_TORCH){
        let a=this.toggles.get(c)||[];
        a=a.filter(x=>x>this.now-60); a.push(this.now); this.toggles.set(c,a);
        if(a.length>8){this.burned.add(c);this.w.lit[c]=0;fired.push(c);continue;}
      }
      this.apply(c,t); fired.push(c);
    }
    if(fired.length) this.settle(fired);
    return fired.length;
  }
  runUntilStable(max=40000){
    const s=this.now; let n=0;
    while(this.queue.length&&n<max){this.tick();n++;}
    return this.now-s;
  }
  run(n){for(let i=0;i<n;i++)this.tick();return n;}
  setLever(i,on){
    if(this.w.lit[i]===(on?1:0))return;
    this.w.lit[i]=on?1:0;
    this.settle([i]);
  }
  high(i){
    const w=this.w,k=w.kind[i];
    if(k===K_WIRE)return w.power[i]>0;
    if(k===K_TORCH||k===K_REP||k===K_LEVER)return !!w.lit[i];
    if(k===K_CMP||k===K_LAMP)return w.power[i]>0;
    if(CONDUCT(k))return this.blockPower(i)>0;
    return false;
  }
}
