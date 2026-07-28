"""Validate the engine against textbook Minecraft redstone behaviour."""

import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from rscalc.engine import World, Engine


def floor(w, x0, x1, z0, z1, y=-1):
    for x in range(x0, x1 + 1):
        for z in range(z0, z1 + 1):
            w.solid((x, y, z))


def test_dust_decay():
    """Dust loses one signal level per block; 15 blocks is the limit."""
    w = World()
    floor(w, -1, 20, 0, 0)
    w.lever((-1, 0, 0), attach="down", on=False)
    for x in range(0, 20):
        w.wire((x, 0, 0))
    e = Engine(w)
    e.set_lever((-1, 0, 0), True)
    e.run_until_stable()
    assert e.read((0, 0, 0)) == 15, e.read((0, 0, 0))
    for i in range(0, 16):
        assert e.read((i, 0, 0)) == 15 - i, (i, e.read((i, 0, 0)))
    assert e.read((15, 0, 0)) == 0
    assert e.read((16, 0, 0)) == 0
    print("  dust decay 15->0 over 15 blocks: OK")


def test_weak_power_does_not_reach_dust():
    """A block powered only by dust must not re-power dust on its far side."""
    w = World()
    floor(w, -1, 6, 0, 0)
    w.lever((-1, 0, 0), attach="down", on=False)
    w.wire((0, 0, 0))
    w.wire((1, 0, 0))
    w.solid((2, 0, 0))          # dust points into this block -> weakly powered
    w.wire((3, 0, 0))           # must stay dark
    e = Engine(w)
    e.set_lever((-1, 0, 0), True)
    e.run_until_stable()
    assert e.read((1, 0, 0)) > 0
    assert e.weak_power((2, 0, 0)) > 0, "block should be weakly powered"
    assert e.strong_power((2, 0, 0)) == 0
    assert e.read((3, 0, 0)) == 0, "weak power must not reach dust"
    print("  weak power blocked from dust: OK")


def test_strong_power_reaches_dust():
    """A repeater facing into a block strongly powers it, re-powering dust beyond."""
    w = World()
    floor(w, -1, 6, 0, 0)
    w.lever((-1, 0, 0), attach="down", on=False)
    w.wire((0, 0, 0))
    w.repeater((1, 0, 0), facing="east", delay=1)
    w.solid((2, 0, 0))
    w.wire((3, 0, 0))
    e = Engine(w)
    e.set_lever((-1, 0, 0), True)
    e.run_until_stable()
    assert e.strong_power((2, 0, 0)) == 15
    assert e.read((3, 0, 0)) == 15, "strong power should give dust full 15"
    print("  strong power re-powers dust at 15: OK")


def test_torch_inverter_and_timing():
    """Classic inverter: dust into a block, torch on the far side. 2 gt delay."""
    w = World()
    floor(w, -2, 4, 0, 0)
    w.lever((-2, 0, 0), attach="down", on=False)
    w.wire((-1, 0, 0))
    w.wire((0, 0, 0))
    w.solid((1, 0, 0))
    w.torch((2, 0, 0), attach="west")
    w.wire((3, 0, 0))
    e = Engine(w)
    e.run_until_stable()
    assert e.high((2, 0, 0)), "torch idles lit"
    assert e.read((3, 0, 0)) == 15

    e.set_lever((-2, 0, 0), True)
    assert e.high((2, 0, 0)), "torch still lit at t+0"
    e.tick()
    assert e.high((2, 0, 0)), "torch still lit at t+1"
    e.tick()
    assert not e.high((2, 0, 0)), "torch must go out exactly 2 game ticks later"
    assert e.read((3, 0, 0)) == 0
    print("  torch inverter, 2 game tick delay: OK")


def test_repeater_delay_settings():
    for setting, expect in ((1, 2), (2, 4), (3, 6), (4, 8)):
        w = World()
        floor(w, -2, 4, 0, 0)
        w.lever((-2, 0, 0), attach="down", on=False)
        w.wire((-1, 0, 0))
        w.repeater((0, 0, 0), facing="east", delay=setting)
        w.wire((1, 0, 0))
        e = Engine(w)
        e.run_until_stable()
        e.set_lever((-2, 0, 0), True)
        n = 0
        while not e.high((1, 0, 0)):
            e.tick()
            n += 1
            assert n < 40
        assert n == expect, f"delay {setting}: expected {expect} gt, got {n}"
    print("  repeater delays 2/4/6/8 game ticks: OK")


def test_repeater_restores_signal():
    w = World()
    floor(w, -1, 30, 0, 0)
    w.lever((-1, 0, 0), attach="down", on=False)
    for x in range(0, 14):
        w.wire((x, 0, 0))
    w.repeater((14, 0, 0), facing="east")
    for x in range(15, 25):
        w.wire((x, 0, 0))
    e = Engine(w)
    e.set_lever((-1, 0, 0), True)
    e.run_until_stable()
    assert e.read((13, 0, 0)) == 2
    assert e.read((15, 0, 0)) == 15, "repeater must restore to 15"
    print("  repeater restores signal to 15: OK")


def test_repeater_is_a_diode():
    w = World()
    floor(w, -1, 6, 0, 0)
    w.wire((0, 0, 0))
    w.repeater((1, 0, 0), facing="east")
    w.wire((2, 0, 0))
    w.lever((3, 0, 0), attach="down", on=True)
    e = Engine(w)
    e.run_until_stable()
    assert e.read((2, 0, 0)) == 15
    assert e.read((0, 0, 0)) == 0, "signal must not pass backwards through a repeater"
    print("  repeater one-way: OK")


def test_repeater_locking():
    w = World()
    floor(w, -2, 6, -2, 2)
    w.lever((-2, 0, 0), attach="down", on=False)
    w.wire((-1, 0, 0))
    w.repeater((0, 0, 0), facing="east", delay=1)
    w.wire((1, 0, 0))
    # locking repeater pointing into the side of the first one
    w.lever((0, 0, 2), attach="down", on=False)
    w.wire((0, 0, 1) if False else (0, 0, 1))
    w.repeater((0, 0, 1), facing="north", delay=1)
    e = Engine(w)
    e.run_until_stable()
    e.set_lever((0, 0, 2), True)
    e.run_until_stable()
    assert w.blocks[(0, 0, 0)].locked, "repeater should be locked"
    e.set_lever((-2, 0, 0), True)
    e.run(20)
    assert e.read((1, 0, 0)) == 0, "locked repeater must not change output"
    e.set_lever((0, 0, 2), False)
    e.run_until_stable()
    assert e.read((1, 0, 0)) == 15, "unlocking should release the new value"
    print("  repeater locking: OK")


def test_comparator_subtract():
    w = World()
    floor(w, -3, 3, -1, 8)
    # rear sees a full 15; the side is fed down a 6-block run so it arrives at 10
    w.lever((-2, 0, 0), attach="down", on=True)
    w.wire((-1, 0, 0))
    w.comparator((0, 0, 0), facing="east", mode="subtract")
    w.wire((1, 0, 0))
    w.lever((0, 0, 7), attach="down", on=True)
    for z in range(1, 7):
        w.wire((0, 0, z))
    e = Engine(w)
    e.run_until_stable()
    rear = e.read((-1, 0, 0))
    side = e.read((0, 0, 1))
    out = e.read((1, 0, 0))
    assert (rear, side) == (15, 10), f"fixture wrong: rear={rear} side={side}"
    assert out == max(rear - side, 0) == 5, f"rear={rear} side={side} out={out}"
    print(f"  comparator subtract ({rear}-{side}={out}): OK")


def test_comparator_compare():
    w = World()
    floor(w, -6, 6, -6, 6)
    w.lever((-6, 0, 0), attach="down", on=True)
    for x in range(-5, 0):
        w.wire((x, 0, 0))
    w.comparator((0, 0, 0), facing="east", mode="compare")
    w.wire((1, 0, 0))
    e = Engine(w)
    e.run_until_stable()
    rear = e.read((-1, 0, 0))
    assert e.read((1, 0, 0)) == rear, "compare mode passes rear through when sides are lower"
    # now raise the side above the rear
    w2 = World()
    floor(w2, -6, 6, -6, 6)
    w2.lever((-6, 0, 0), attach="down", on=True)
    for x in range(-5, 0):
        w2.wire((x, 0, 0))
    w2.comparator((0, 0, 0), facing="east", mode="compare")
    w2.wire((1, 0, 0))
    w2.redstone_block((0, 0, 1))     # side input 15 > rear
    e2 = Engine(w2)
    e2.run_until_stable()
    assert e2.read((1, 0, 0)) == 0, "compare mode blocks when a side exceeds the rear"
    print("  comparator compare mode: OK")


def test_torch_burnout():
    """A torch forced off more than 8 times in 60 gt burns out."""
    w = World()
    floor(w, -2, 4, 0, 0)
    w.lever((-2, 0, 0), attach="down", on=False)
    w.wire((-1, 0, 0)); w.wire((0, 0, 0))
    w.solid((1, 0, 0))
    w.torch((2, 0, 0), attach="west")
    e = Engine(w)
    e.run_until_stable()
    for _ in range(12):
        e.set_lever((-2, 0, 0), True); e.run(2)
        e.set_lever((-2, 0, 0), False); e.run(2)
    assert (2, 0, 0) in e.burned_out, "torch should have burned out"
    print("  torch burnout: OK")


def test_staircase_dust():
    """Dust climbs a one-block step."""
    w = World()
    floor(w, -1, 6, 0, 0)
    w.lever((-1, 0, 0), attach="down", on=True)
    w.wire((0, 0, 0))
    w.solid((1, 0, 0))
    w.wire((1, 1, 0))
    w.solid((2, 0, 0))
    w.wire((2, 1, 0))
    e = Engine(w)
    e.run_until_stable()
    assert e.read((1, 1, 0)) == 14, e.read((1, 1, 0))
    assert e.read((2, 1, 0)) == 13
    print("  dust staircase: OK")


if __name__ == "__main__":
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    print(f"Running {len(tests)} redstone mechanics tests\n")
    failed = 0
    for t in tests:
        try:
            t()
        except AssertionError as ex:
            print(f"  FAIL {t.__name__}: {ex}")
            failed += 1
        except Exception as ex:
            print(f"  ERROR {t.__name__}: {type(ex).__name__}: {ex}")
            failed += 1
    print(f"\n{len(tests) - failed}/{len(tests)} passed")
    sys.exit(1 if failed else 0)
