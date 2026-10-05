// Export an analyzed program into a private, resumable research index.
// @category SMR.Research
// @menupath Tools.SMR.Export Engine Research
// Arguments: private-output-directory [per-function-timeout-seconds=20]
// Run against an independent executable copy; this script does not patch the program.

import ghidra.app.script.GhidraScript;
import ghidra.app.decompiler.DecompInterface;
import ghidra.app.decompiler.DecompileResults;
import ghidra.framework.Application;
import ghidra.program.model.address.*;
import ghidra.program.model.listing.*;
import ghidra.program.model.symbol.*;
import ghidra.program.model.data.StringDataInstance;
import com.google.gson.*;
import java.io.*;
import java.nio.channels.*;
import java.nio.charset.StandardCharsets;
import java.nio.file.*;
import java.security.MessageDigest;
import java.time.Instant;
import java.util.*;

public class ExportEngine extends GhidraScript {
    private static final int FORMAT = 1;
    private static final Gson JSON = new GsonBuilder().disableHtmlEscaping().serializeNulls().create();
    private Path output;
    private String binaryHash;
    private String toolVersion;
    private long total, completed, reused, failed, external, references, calls, strings, stringRefs;

    @Override public void run() throws Exception {
        String[] args = getScriptArgs();
        if (args.length < 1 || args.length > 2) {
            throw new IllegalArgumentException("Expected private-output-directory [timeout-seconds]");
        }
        int timeout = args.length == 2 ? Integer.parseInt(args[1]) : 20;
        if (timeout < 1 || timeout > 3600) throw new IllegalArgumentException("Timeout must be 1..3600 seconds");
        output = Paths.get(args[0]).toAbsolutePath().normalize();
        Files.createDirectories(output);
        if (Files.isSymbolicLink(output)) throw new IOException("Output directory must not be a symbolic link");
        output = output.toRealPath();
        binaryHash = currentProgram.getExecutableSHA256();
        if (binaryHash == null || !binaryHash.matches("(?i)[0-9a-f]{64}")) {
            throw new IOException("Program has no valid imported binary SHA-256; import the executable first");
        }
        binaryHash = binaryHash.toLowerCase(Locale.ROOT);
        toolVersion = Application.getApplicationVersion();
        try (FileChannel channel = FileChannel.open(output.resolve("export.lock"),
                StandardOpenOption.CREATE, StandardOpenOption.WRITE);
             FileLock lock = channel.tryLock()) {
            if (lock == null) throw new IOException("Another export owns this output directory");
            export(timeout);
        }
    }

    private void export(int timeout) throws Exception {
        JsonObject identity = new JsonObject();
        identity.addProperty("schema", FORMAT);
        identity.addProperty("binary_sha256", binaryHash);
        identity.addProperty("ghidra_version", toolVersion);
        identity.addProperty("image_base", currentProgram.getImageBase().toString());
        identity.addProperty("language", currentProgram.getLanguageID().toString());
        identity.addProperty("compiler", currentProgram.getCompilerSpec().getCompilerSpecID().toString());
        Path identityFile = output.resolve("identity.json");
        if (Files.exists(identityFile)) {
            JsonObject previous = readJson(identityFile);
            if (!identity.equals(previous)) throw new IOException("Output identity differs; choose a new directory");
        } else atomic(identityFile, JSON.toJson(identity) + "\n");
        Files.createDirectories(output.resolve("functions"));
        Files.deleteIfExists(output.resolve("COMPLETE.json"));
        atomic(output.resolve("RUNNING.json"), JSON.toJson(progress("running")) + "\n");
        DecompInterface decompiler = new DecompInterface();
        try {
            decompiler.toggleCCode(true);
            decompiler.toggleSyntaxTree(false);
            if (!decompiler.openProgram(currentProgram)) {
                throw new IOException("Cannot open decompiler: " + decompiler.getLastMessage());
            }
            try (BufferedWriter index = writer("functions.jsonl.partial")) {
                FunctionIterator functions = currentProgram.getFunctionManager().getFunctions(true);
                while (functions.hasNext()) exportFunction(functions.next(), decompiler, timeout, index);
                FunctionIterator externals = currentProgram.getFunctionManager().getExternalFunctions();
                while (externals.hasNext()) exportFunction(externals.next(), decompiler, timeout, index);
            }
            publish("functions.jsonl");
            exportReferences();
            exportStrings();
            JsonObject summary = progress("complete");
            summary.addProperty("per_function_timeout_seconds", timeout);
            summary.addProperty("scope", "Every function discovered in this analyzed program; identified strings and static references. Indirect calls, undiscovered code and original source types may remain unresolved.");
            summary.addProperty("all_discovered_internal_functions_decompiled", failed == 0);
            atomic(output.resolve("summary.json"), JSON.toJson(summary) + "\n");
            atomic(output.resolve("COMPLETE.json"), JSON.toJson(summary) + "\n");
            Files.deleteIfExists(output.resolve("RUNNING.json"));
            println("Export complete: " + total + " functions; " + completed + " decompiled; " + failed + " failed; " + external + " external");
        } catch (Exception error) {
            JsonObject summary = progress("interrupted_or_failed");
            summary.addProperty("error", error.toString());
            atomic(output.resolve("summary.json"), JSON.toJson(summary) + "\n");
            throw error;
        } finally {
            decompiler.dispose();
        }
    }

    private void exportFunction(Function function, DecompInterface decompiler,
                                int timeout, BufferedWriter index) throws Exception {
        monitor.checkCancelled();
        total++;
        Address entry = function.getEntryPoint();
        String key = entry.toString(true).replaceAll("[^a-zA-Z0-9_-]", "_");
        Path meta = output.resolve("functions").resolve(key + ".json");
        Path code = output.resolve("functions").resolve(key + ".c");
        JsonObject item = location(entry);
        item.addProperty("name", function.getName(true));
        item.addProperty("signature", function.getPrototypeString(true, true));
        item.addProperty("external", function.isExternal());
        item.addProperty("thunk", function.isThunk());
        item.addProperty("binary_sha256", binaryHash);
        String bodyError = "";
        try { item.addProperty("body_sha256", bodyHash(function)); }
        catch (ghidra.program.model.mem.MemoryAccessException | IOException unreadable) {
            // Analysis can discover a function in uninitialized memory. Preserve
            // that failure in the inventory rather than aborting every later stage.
            item.add("body_sha256", JsonNull.INSTANCE);
            bodyError = "Unreadable function body: " + unreadable;
        }
        item.addProperty("body_address_count", function.getBody().getNumAddresses());
        JsonArray ranges = new JsonArray();
        AddressRangeIterator rangeIterator = function.getBody().getAddressRanges();
        while (rangeIterator.hasNext()) {
            AddressRange range = rangeIterator.next();
            JsonObject r = new JsonObject();
            r.addProperty("start", range.getMinAddress().toString());
            r.addProperty("end", range.getMaxAddress().toString());
            ranges.add(r);
        }
        item.add("body_ranges", ranges);
        String status = "failed";
        String error = bodyError;
        boolean cached = false;
        if (bodyError.isEmpty() && !function.isExternal() && Files.isRegularFile(meta) && Files.isRegularFile(code)) {
            try {
                JsonObject previous = readJson(meta);
                cached = "decompiled".equals(previous.get("status").getAsString()) &&
                    item.get("body_sha256").equals(previous.get("body_sha256")) &&
                    item.get("signature").equals(previous.get("signature")) &&
                    item.get("name").equals(previous.get("name")) &&
                    binaryHash.equals(previous.get("binary_sha256").getAsString()) &&
                    sha(Files.readAllBytes(code)).equals(previous.get("decomp_sha256").getAsString());
                if (cached) { status = "decompiled"; reused++; }
            } catch (Exception invalidCache) { cached = false; }
        }
        if (function.isExternal()) {
            status = "external";
            external++;
            atomic(code, "/* External function; no implementation present in this executable.\n" +
                    safeComment(function.getName(true)) + "\n" + safeComment(function.getPrototypeString(true, true)) + "\n*/\n");
        } else if (!bodyError.isEmpty()) {
            Files.deleteIfExists(code);
        } else if (!cached) {
            long start = System.nanoTime();
            try {
                DecompileResults result = decompiler.decompileFunction(function, timeout, monitor);
                monitor.checkCancelled();
                if (result != null && result.decompileCompleted() && result.getDecompiledFunction() != null) {
                    atomic(code, "/* Reconstructed pseudocode, not original source. Binary SHA-256: " +
                        binaryHash + "; entry: " + entry + " */\n" + result.getDecompiledFunction().getC());
                    status = "decompiled";
                    error = result.getErrorMessage();
                } else {
                    error = result == null ? "No decompiler result" : result.getErrorMessage();
                    Files.deleteIfExists(code);
                }
            } catch (ghidra.util.exception.CancelledException cancelled) { throw cancelled; }
              catch (Exception failure) { error = failure.toString(); Files.deleteIfExists(code); }
            item.addProperty("elapsed_seconds", (System.nanoTime() - start) / 1_000_000_000.0);
        }
        if ("decompiled".equals(status)) completed++;
        else if (!function.isExternal()) failed++;
        item.addProperty("status", status);
        item.addProperty("error", error == null ? "" : error);
        item.addProperty("reused", cached);
        item.addProperty("decomp_file", Files.isRegularFile(code) ? output.relativize(code).toString() : "");
        item.addProperty("decomp_sha256", Files.isRegularFile(code) ? sha(Files.readAllBytes(code)) : "");
        atomic(meta, JSON.toJson(item) + "\n");
        line(index, item);
        if (total % 100 == 0) {
            index.flush();
            atomic(output.resolve("RUNNING.json"), JSON.toJson(progress("decompiling")) + "\n");
            println("Exported " + total + " functions; failures " + failed + "; reused " + reused);
        }
    }

    private void exportReferences() throws Exception {
        FunctionManager manager = currentProgram.getFunctionManager();
        ReferenceManager refs = currentProgram.getReferenceManager();
        try (BufferedWriter all = writer("references.jsonl.partial");
             BufferedWriter graph = writer("callgraph.jsonl.partial")) {
            AddressIterator sources = refs.getReferenceSourceIterator(currentProgram.getMemory(), true);
            while (sources.hasNext()) {
                monitor.checkCancelled();
                Address source = sources.next();
                for (Reference ref : refs.getReferencesFrom(source)) {
                    JsonObject item = reference(ref);
                    line(all, item); references++;
                    if (ref.getReferenceType().isCall()) { line(graph, item); calls++; }
                }
            }
            InstructionIterator instructions = currentProgram.getListing().getInstructions(true);
            while (instructions.hasNext()) {
                monitor.checkCancelled();
                Instruction instruction = instructions.next();
                if (!instruction.getFlowType().isCall()) continue;
                boolean resolved = false;
                for (Reference ref : instruction.getReferencesFrom()) if (ref.getReferenceType().isCall()) resolved = true;
                if (!resolved) {
                    JsonObject item = location(instruction.getAddress());
                    item.addProperty("from", instruction.getAddress().toString());
                    item.add("to", JsonNull.INSTANCE);
                    item.addProperty("type", "unresolved_call");
                    Function caller = manager.getFunctionContaining(instruction.getAddress());
                    item.addProperty("caller", caller == null ? "" : caller.getEntryPoint().toString());
                    item.addProperty("instruction", instruction.toString());
                    line(graph, item); calls++;
                }
            }
        }
        publish("references.jsonl"); publish("callgraph.jsonl");
    }

    private void exportStrings() throws Exception {
        try (BufferedWriter index = writer("strings.jsonl.partial");
             BufferedWriter refs = writer("string-references.jsonl.partial")) {
            DataIterator values = currentProgram.getListing().getDefinedData(true);
            while (values.hasNext()) {
                monitor.checkCancelled();
                Data value = values.next();
                if (!value.hasStringValue()) continue;
                StringDataInstance string = StringDataInstance.getStringDataInstance(value);
                String text = string.getStringValue();
                if (text == null) continue;
                JsonObject item = location(value.getAddress());
                item.addProperty("value", text);
                item.addProperty("length_bytes", value.getLength());
                item.addProperty("datatype", value.getDataType().getDisplayName());
                line(index, item); strings++;
                ReferenceIterator incoming = currentProgram.getReferenceManager().getReferencesTo(value.getAddress());
                while (incoming.hasNext()) { line(refs, reference(incoming.next())); stringRefs++; }
            }
        }
        publish("strings.jsonl"); publish("string-references.jsonl");
    }

    private JsonObject reference(Reference reference) {
        JsonObject item = new JsonObject();
        item.addProperty("from", reference.getFromAddress().toString());
        item.addProperty("to", reference.getToAddress().toString());
        item.addProperty("type", reference.getReferenceType().toString());
        item.addProperty("operand_index", reference.getOperandIndex());
        item.addProperty("source", reference.getSource().toString());
        FunctionManager manager = currentProgram.getFunctionManager();
        Function caller = manager.getFunctionContaining(reference.getFromAddress());
        Function target = manager.getFunctionAt(reference.getToAddress());
        item.addProperty("caller", caller == null ? "" : caller.getEntryPoint().toString());
        item.addProperty("target_function", target == null ? "" : target.getEntryPoint().toString());
        return item;
    }

    private String bodyHash(Function function) throws Exception {
        MessageDigest digest = MessageDigest.getInstance("SHA-256");
        if (!function.isExternal()) {
            AddressRangeIterator ranges = function.getBody().getAddressRanges();
            byte[] buffer = new byte[65536];
            while (ranges.hasNext()) {
                AddressRange range = ranges.next();
                digest.update(range.toString().getBytes(StandardCharsets.UTF_8));
                Address cursor = range.getMinAddress();
                long remaining = range.getLength();
                while (remaining > 0) {
                    monitor.checkCancelled();
                    int wanted = (int)Math.min(buffer.length, remaining);
                    int count = currentProgram.getMemory().getBytes(cursor, buffer, 0, wanted);
                    if (count != wanted) throw new IOException("Incomplete function body read: " + cursor);
                    digest.update(buffer, 0, count); remaining -= count;
                    if (remaining > 0) cursor = cursor.add(count);
                }
            }
        }
        return HexFormat.of().formatHex(digest.digest());
    }
    private JsonObject location(Address address) {
        JsonObject result = new JsonObject();
        result.addProperty("address", address.toString());
        try { result.addProperty("image_offset", "0x" + Long.toUnsignedString(address.subtract(currentProgram.getImageBase()), 16)); }
        catch (Exception differentSpace) { result.add("image_offset", JsonNull.INSTANCE); }
        return result;
    }
    private JsonObject progress(String status) {
        JsonObject value = new JsonObject();
        value.addProperty("schema", FORMAT); value.addProperty("status", status);
        value.addProperty("timestamp", Instant.now().toString());
        value.addProperty("binary_sha256", binaryHash); value.addProperty("ghidra_version", toolVersion);
        value.addProperty("functions_seen", total); value.addProperty("decompiled", completed);
        value.addProperty("reused", reused); value.addProperty("failed", failed); value.addProperty("external", external);
        value.addProperty("references", references); value.addProperty("call_edges", calls);
        value.addProperty("strings", strings); value.addProperty("string_references", stringRefs);
        return value;
    }
    private BufferedWriter writer(String name) throws IOException {
        return Files.newBufferedWriter(output.resolve(name), StandardCharsets.UTF_8,
            StandardOpenOption.CREATE, StandardOpenOption.TRUNCATE_EXISTING, StandardOpenOption.WRITE);
    }
    private void publish(String name) throws IOException { move(output.resolve(name + ".partial"), output.resolve(name)); }
    private static void line(BufferedWriter writer, JsonObject value) throws IOException { writer.write(JSON.toJson(value)); writer.newLine(); }
    private static JsonObject readJson(Path path) throws IOException {
        try (Reader reader = Files.newBufferedReader(path, StandardCharsets.UTF_8)) { return JsonParser.parseReader(reader).getAsJsonObject(); }
    }
    private static void atomic(Path target, String text) throws IOException {
        Path temp = Files.createTempFile(target.getParent(), ".export-", ".tmp");
        try { Files.writeString(temp, text, StandardCharsets.UTF_8); move(temp, target); }
        finally { Files.deleteIfExists(temp); }
    }
    private static void move(Path source, Path destination) throws IOException {
        try { Files.move(source, destination, StandardCopyOption.ATOMIC_MOVE, StandardCopyOption.REPLACE_EXISTING); }
        catch (AtomicMoveNotSupportedException unsupported) { Files.move(source, destination, StandardCopyOption.REPLACE_EXISTING); }
    }
    private static String sha(byte[] bytes) throws Exception { return HexFormat.of().formatHex(MessageDigest.getInstance("SHA-256").digest(bytes)); }
    private static String safeComment(String text) { return text.replace("*/", "* /"); }
}
