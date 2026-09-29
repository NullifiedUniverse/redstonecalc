import com.google.gson.JsonElement;
import com.google.gson.JsonObject;
import com.google.gson.JsonParser;
import com.mojang.brigadier.CommandDispatcher;
import com.mojang.serialization.DataResult;
import com.mojang.serialization.JsonOps;
import net.minecraft.server.packs.PackType;
import net.minecraft.server.packs.metadata.pack.PackFormat;
import net.minecraft.server.packs.metadata.pack.PackMetadataSection;
import net.minecraft.SharedConstants;
import net.minecraft.commands.CommandBuildContext;
import net.minecraft.commands.CommandSourceStack;
import net.minecraft.commands.Commands;
import net.minecraft.commands.functions.CommandFunction;
import net.minecraft.commands.functions.MacroFunction;
import net.minecraft.core.HolderLookup;
import net.minecraft.data.registries.VanillaRegistries;
import net.minecraft.nbt.CompoundTag;
import net.minecraft.nbt.IntTag;
import net.minecraft.nbt.TagParser;
import net.minecraft.resources.Identifier;
import net.minecraft.server.Bootstrap;
import net.minecraft.server.permissions.LevelBasedPermissionSet;

import java.nio.file.Files;
import java.nio.file.Path;
import java.util.ArrayList;
import java.util.List;
import java.util.TreeSet;
import java.util.regex.Matcher;
import java.util.regex.Pattern;
import java.util.stream.Stream;

/**
 * Load every function in a datapack with the game's own function compiler.
 *
 *   java -cp <game classpath>:<this> Oracle <pack dir> ['{macro:args}']
 *
 * This is the ground truth that rscalc/mccheck.py approximates. It boots the
 * real registries, builds the real Brigadier dispatcher and hands each file to
 * CommandFunction.fromLines — the call the server makes when a pack loads — so
 * "does this function load" is answered by the code that will answer it in
 * game. Macro functions are instantiated with the argument tags given (default:
 * the integer 1 for every parameter), which is the step where `$` lines are
 * substituted and parsed.
 *
 * No world, no server and no EULA: this never starts a server.
 *
 * Prints one `FAIL <function> <why>` line per function the game would reject,
 * and a final `SUMMARY <functions> <macro functions> <failed>`.
 *
 * A third, `Oracle --mcmeta <file>`, reads a `pack.mcmeta` through the game's own
 * PackMetadataSection codec and says whether it parses and whether the game
 * would call the pack *compatible* with the version running it — which is the
 * difference between loading quietly and "made for an older version".
 *
 * A second mode, `Oracle --lines <file>`, treats every line of the file as its
 * own one-line function and prints `<index> OK` or `<index> ERR <why>`. That is
 * what lets rscalc/mccheck.py be tested against the game across thousands of
 * commands, including deliberately wrong ones, instead of trusted.
 */
public class Oracle {
    private static final Pattern MACRO = Pattern.compile("\\$\\(([A-Za-z0-9_]+)\\)");

    public static void main(String[] args) throws Exception {
        if (args[0].equals("--mcmeta")) {
            SharedConstants.tryDetectVersion();
            Bootstrap.bootStrap();
            JsonObject root = JsonParser.parseString(Files.readString(Path.of(args[1]))).getAsJsonObject();
            JsonElement pack = root.get("pack");
            DataResult<PackMetadataSection> r =
                PackMetadataSection.forPackType(PackType.SERVER_DATA).codec().parse(JsonOps.INSTANCE, pack);
            PackFormat current = SharedConstants.getCurrentVersion().packVersion(PackType.SERVER_DATA);
            if (r.isError()) {
                Bootstrap.realStdoutPrintln("MCMETA ERR current=" + current + " "
                    + r.error().get().message().replace('\n', ' '));
            } else {
                PackMetadataSection m = r.result().get();
                boolean ok = m.supportedFormats().isValueInRange(current);
                Bootstrap.realStdoutPrintln("MCMETA OK current=" + current + " range="
                    + m.supportedFormats().minInclusive() + ".." + m.supportedFormats().maxInclusive()
                    + " compatible=" + ok);
            }
            return;
        }
        boolean lineMode = args[0].equals("--lines");
        Path pack = Path.of(lineMode ? args[1] : args[0]);
        int next = lineMode ? 2 : 1;
        CompoundTag given = args.length > next ? TagParser.parseCompoundFully(args[next]) : new CompoundTag();

        SharedConstants.tryDetectVersion();
        Bootstrap.bootStrap();
        // 26.2 calls it createLookup, 26.3 createWorldLookup: same thing, and a
        // rename between two point releases is exactly what reflection is for
        HolderLookup.Provider lookup;
        try {
            lookup = (HolderLookup.Provider) VanillaRegistries.class.getMethod("createLookup").invoke(null);
        } catch (NoSuchMethodException e) {
            lookup = (HolderLookup.Provider) VanillaRegistries.class.getMethod("createWorldLookup").invoke(null);
        }
        CommandBuildContext ctx = Commands.createValidationContext(lookup);
        CommandDispatcher<CommandSourceStack> dispatcher =
            new Commands(Commands.CommandSelection.ALL, ctx).getDispatcher();
        CommandSourceStack source = Commands.createCompilationContext(LevelBasedPermissionSet.OWNER);

        if (lineMode) {
            List<String> all = Files.readAllLines(pack);
            Identifier id = Identifier.fromNamespaceAndPath("test", "line");
            for (int i = 0; i < all.size(); i++) {
                String line = all.get(i);
                String verdict = "OK";
                try {
                    CommandFunction<CommandSourceStack> fn =
                        CommandFunction.fromLines(id, dispatcher, source, List.of(line));
                    if (fn instanceof MacroFunction<?>) {
                        CompoundTag arguments = new CompoundTag();
                        Matcher m = MACRO.matcher(line);
                        while (m.find()) {
                            String n = m.group(1);
                            arguments.put(n, given.get(n) != null ? given.get(n) : IntTag.valueOf(1));
                        }
                        fn.instantiate(arguments, dispatcher);
                    }
                } catch (Exception e) {
                    verdict = "ERR\t" + String.valueOf(e.getMessage()).replace('\n', ' ');
                }
                Bootstrap.realStdoutPrintln(i + "\t" + verdict);
            }
            return;
        }

        int total = 0, macros = 0, failed = 0;
        Path data = pack.resolve("data");
        List<Path> namespaces = new ArrayList<>();
        try (Stream<Path> s = Files.list(data)) { s.sorted().forEach(namespaces::add); }

        for (Path nsDir : namespaces) {
            Path fdir = nsDir.resolve("function");
            if (!Files.isDirectory(fdir)) continue;
            String ns = nsDir.getFileName().toString();
            List<Path> files = new ArrayList<>();
            try (Stream<Path> s = Files.walk(fdir)) {
                s.filter(p -> p.toString().endsWith(".mcfunction")).sorted().forEach(files::add);
            }
            for (Path file : files) {
                String rel = fdir.relativize(file).toString().replace('\\', '/');
                rel = rel.substring(0, rel.length() - ".mcfunction".length());
                Identifier id = Identifier.fromNamespaceAndPath(ns, rel);
                List<String> lines = Files.readAllLines(file);
                total++;
                try {
                    CommandFunction<CommandSourceStack> fn =
                        CommandFunction.fromLines(id, dispatcher, source, lines);
                    if (fn instanceof MacroFunction<?>) {
                        macros++;
                        TreeSet<String> names = new TreeSet<>();
                        for (String l : lines) {
                            if (!l.startsWith("$")) continue;
                            Matcher m = MACRO.matcher(l);
                            while (m.find()) names.add(m.group(1));
                        }
                        CompoundTag arguments = new CompoundTag();
                        for (String n : names) {
                            arguments.put(n, given.get(n) != null ? given.get(n) : IntTag.valueOf(1));
                        }
                        fn.instantiate(arguments, dispatcher);
                    }
                } catch (Exception e) {
                    failed++;
                    String why = String.valueOf(e.getMessage()).replace('\n', ' ');
                    Bootstrap.realStdoutPrintln("FAIL " + id + " " + why);
                }
            }
        }
        Bootstrap.realStdoutPrintln("SUMMARY " + total + " " + macros + " " + failed);
    }
}
