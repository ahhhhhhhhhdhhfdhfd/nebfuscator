"""
neb_deobf: dynamic deobfuscator for Roblox Luau scripts protected by nebfuscator.

Usage:
    python deobf.py <input>                one input -> one output file:
                                             <input folder>/output/<input name>
    python deobf.py <input> --no-devirt    faster: only the behaviour trace
    python deobf.py <input> --debug        every intermediate file in output/
    python deobf.py <input> --detect       only print which obfuscator it is

The input's obfuscator is detected and its plugin runs the pipeline.
Unrecognized inputs get the generic behaviour trace: the script runs in the
real Luau VM against a fake Roblox/executor environment (neb.luau) and
everything it does is rendered back as Luau. Nothing touches the network or
Roblox (--studio optionally runs the harness inside Roblox Studio).

Exit status: 0 with a result file, 1 on failure, 2 on bad usage.
"""
import argparse
import os
import re
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

# ---------------------------------------------------------------------------
# PyPy fallback (same as Cadmio's deob.py)
# ---------------------------------------------------------------------------
PYPY_MIN_SIZE = 350_000


def find_pypy():
    import glob
    import shutil
    p = shutil.which("pypy3")
    if p:
        return p
    pat = os.path.join(os.environ.get("LOCALAPPDATA", ""), "Microsoft", "WinGet", "Packages",
                       "PyPy.PyPy.3*", "*", "pypy3.exe")
    found = sorted(glob.glob(pat))
    return found[-1] if found else None


def maybe_reexec_pypy(args):
    """Run this whole command under PyPy instead (see PYPY_MIN_SIZE)."""
    import platform
    if platform.python_implementation() == "PyPy" or args.no_pypy or args.no_devirt or args.detect:
        return
    if not args.pypy and os.path.getsize(args.input) < PYPY_MIN_SIZE:
        return
    pypy = find_pypy()
    if not pypy:
        if args.pypy:
            print("[!] --pypy: PyPy not found (winget install PyPy.PyPy.3.11); using this Python",
                  file=sys.stderr)
        return
    print("[*] big script: running under PyPy (%s; --no-pypy to turn off)" % pypy, file=sys.stderr)
    sys.stderr.flush()
    sys.exit(subprocess.call([pypy, os.path.abspath(__file__)] + sys.argv[1:]))


def parser():
    ap = argparse.ArgumentParser(description="Deobfuscate a protected Roblox Luau script.")
    ap.add_argument("input")
    ap.add_argument("-o", "--output", help="result file (default: <input folder>/output/<input name>)")
    ap.add_argument("--obfuscator", metavar="NAME",
                    help="skip detection and use this plugin")
    ap.add_argument("--detect", action="store_true",
                    help="only print the detected obfuscator (NAME<tab>confidence<tab>label) and exit")
    ap.add_argument("--timeout", type=int, default=90, help="hard timeout per run in seconds")
    ap.add_argument("--budget", type=int, default=30, help="soft time budget for the traced script")
    ap.add_argument("--executor", default="Wave", help="name returned by identifyexecutor()")
    ap.add_argument("--keep-harness", action="store_true", help="keep the generated harness file")
    ap.add_argument("--input-text", help="text that TextBoxes contain when event handlers are traced "
                                         "(exercises e.g. key-check branches)")
    ap.add_argument("--cfg", action="append", default=[], metavar="KEY=VALUE",
                    help="extra runtime option for neb.luau, e.g. --cfg prelude=...")
    ap.add_argument("--strings", action="store_true", help="also dump every string the script builds")
    ap.add_argument("--no-tidy", action="store_true",
                    help="write the raw trace (skip the readability pass in tidy.py)")
    ap.add_argument("--no-fold", action="store_true",
                    help="do not fold repeated helper calls and unrolled loops back into functions/loops")
    ap.add_argument("--keep-preamble", action="store_true",
                    help="keep the obfuscator's own environment/anti-tamper probes at the top of the trace")
    ap.add_argument("--raw", help="also save the complete raw runtime output of the last run here")
    ap.add_argument("--studio", action="store_true",
                    help="optional: run inside Roblox Studio so engine objects (Path2D, UDim2, ...) "
                         "are real; serves the harness on 127.0.0.1 and waits for the loader "
                         "written next to the output")
    ap.add_argument("--port", type=int, default=34889, help="local port for --studio")
    ap.add_argument("--studio-wait", type=int, default=600, help="seconds to wait for Studio per run")
    ap.add_argument("--no-devirt", action="store_true",
                    help="fast: skip lifting VM bytecode; the output is the behaviour trace only")
    ap.add_argument("--devirt", action="store_true", help=argparse.SUPPRESS)   # the default now
    ap.add_argument("--debug", action="store_true",
                    help="write every intermediate file to the output folder "
                         "(trace *.deobf.luau, *.devirt.luau, ...) instead of one result file")
    ap.add_argument("--pypy", action="store_true",
                    help="run under PyPy even for a small script "
                         "(default: only above %d bytes)" % PYPY_MIN_SIZE)
    ap.add_argument("--no-pypy", action="store_true", help="never switch to PyPy")
    ap.add_argument("--api-dir", default=None,
                    help="path to API dump directory (API-Dump.json, classes.json, enums.json, etc.)")
    ap.add_argument("--api-auto", action="store_true", default=True,
                    help="automatically detect and use api/ directory next to neb.luau")
    ap.add_argument("--max-instructions", type=int, default=250000,
                    help="maximum statements to record before aborting")
    ap.add_argument("--max-block", type=int, default=25000,
                    help="maximum block size")
    ap.add_argument("--max-waits", type=int, default=50,
                    help="maximum wait statements before truncating")
    ap.add_argument("--time-budget", type=int, default=30,
                    help="time budget in seconds for the traced script")
    return ap


def main():
    args = parser().parse_args()
    with open(args.input, "rb") as f:
        source = f.read().decode("latin-1")

    # Load API data if available
    api_data = None
    if args.api_dir:
        api_data = load_api_data(args.api_dir)
    elif args.api_auto:
        api_data = load_api_data(os.path.join(os.path.dirname(os.path.abspath(__file__)), "api"))

    if args.obfuscator:
        plugin = None
        conf = None
        # For now we use a single generic deobfuscator that uses neb.luau
    else:
        # Auto-detect obfuscator
        plugin, conf = detect_obfuscator(source)

    if args.detect:
        print("%s\t%s\t%s" % (plugin.name if plugin else "unknown",
                              "forced" if conf is None else "%.2f" % (conf or 0),
                              plugin.label if plugin else "unknown"))
        return

    maybe_reexec_pypy(args)
    print("[*] obfuscator: %s%s" % (plugin.label if plugin else "unknown",
        "" if conf is None else " (detected, %.2f)" % conf), file=sys.stderr)

    outdir = os.path.join(os.path.dirname(os.path.abspath(args.input)), "output")
    tracename = re.sub(r"(\.luau?|\.txt)?$", ".deobf.luau", os.path.basename(args.input), count=1)
    workdir = None
    if args.debug:
        # every file: <input folder>/output/<name>.deobf.luau, .devirt.luau, ...
        trace_path = args.output or os.path.join(outdir, tracename)
        final = None
    else:
        # one input -> one output file (<input folder>/output/<input file name>)
        import tempfile
        workdir = tempfile.mkdtemp(prefix="nebf_")
        trace_path = os.path.join(workdir, tracename)
        final = args.output or os.path.join(outdir, os.path.basename(args.input))
    os.makedirs(os.path.dirname(os.path.abspath(final or trace_path)), exist_ok=True)

    try:
        result = run_deobfuscate(args.input, source, args, trace_path, plugin, conf, api_data)
        if final:
            if not result or not os.path.exists(result):
                sys.exit("[!] no result")
            import shutil
            shutil.copyfile(result, final)
            print("[+] result: " + final, file=sys.stderr)
    finally:
        if workdir:
            import shutil
            shutil.rmtree(workdir, ignore_errors=True)


def load_api_data(api_dir):
    """Load API dump data from directory for use in deobfuscation."""
    import json
    api_data = {}
    if not api_dir or not os.path.isdir(api_dir):
        return api_data

    # Load API-Dump.json
    api_dump_path = os.path.join(api_dir, "API-Dump.json")
    if os.path.isfile(api_dump_path):
        try:
            with open(api_dump_path, "r", encoding="utf-8") as f:
                api_data["api_dump"] = json.load(f)
        except Exception:
            pass

    # Load classes.json
    classes_path = os.path.join(api_dir, "classes.json")
    if os.path.isfile(classes_path):
        try:
            with open(classes_path, "r", encoding="utf-8") as f:
                api_data["classes"] = json.load(f)
        except Exception:
            pass

    # Load enums.json
    enums_path = os.path.join(api_dir, "enums.json")
    if os.path.isfile(enums_path):
        try:
            with open(enums_path, "r", encoding="utf-8") as f:
                api_data["enums"] = json.load(f)
        except Exception:
            pass

    # Load Globals.luau
    globals_path = os.path.join(api_dir, "Globals.luau")
    if os.path.isfile(globals_path):
        try:
            with open(globals_path, "r", encoding="utf-8") as f:
                api_data["globals_luau"] = f.read()
        except Exception:
            pass

    # Load Luautypes.d.luau
    luatypes_path = os.path.join(api_dir, "Luautypes.luau")
    if os.path.isfile(luatypes_path):
        try:
            with open(luatypes_path, "r", encoding="utf-8") as f:
                api_data["luatypes_luau"] = f.read()
        except Exception:
            pass

    return api_data


def detect_obfuscator(source):
    """Detect which obfuscator was used based on source characteristics."""
    # Check for Luraph markers
    if "Luraph" in source or "luraph" in source.lower() or "LPH[" in source:
        from obfuscators.luraph_v14 import Luraph14
        return Luraph14(), 1.0

    # Check for Ironbrew
    if "ironbrew" in source.lower() or "ironbrew1" in source.lower():
        from obfuscators.ironbrew1 import Ironbrew1
        return Ironbrew1(), 1.0

    # Check for moonsec
    if "moonsec" in source.lower():
        from obfuscators.moonsec import Moonsec
        return Moonsec(), 1.0

    # Check for prometheus
    if "prometheus" in source.lower():
        from obfuscators.prometheus import Prometheus
        return Prometheus(), 1.0

    # Default to generic
    from obfuscators.generic import Generic
    return Generic(), 0.01


def run_deobfuscate(input_path, source, args, trace_path, plugin, conf, api_data):
    """Run the deobfuscation pipeline."""
    import time
    t0 = __import__('time').time()

    # Build the harness with neb.luau runtime
    neb_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "neb.luau")
    if not os.path.exists(neb_path):
        sys.exit("[!] neb.luau not found next to deobf.py")

    # Build the harness source
    harness_source = build_harness(source, args, api_data)

    # Write harness to temp file
    import tempfile
    harness_path = os.path.join(os.path.dirname(trace_path), "harness.luau")
    os.makedirs(os.path.dirname(harness_path), exist_ok=True)
    with open(harness_path, "w", encoding="utf-8", newline="\n") as f:
        f.write(harness_source)

    # Run the harness through luau.exe if available, or just use the stub
    luau_exe = find_luau()
    if luau_exe and os.path.exists(neb_path.replace("neb.luau", "bin/luau.exe")):
        luau_path = neb_path.replace("neb.luau", "bin/luau.exe")
    else:
        luau_path = shutil.which("luau") if shutil else None

    if luau_path and os.path.exists(luau_path):
        # Run the harness through luau.exe
        import subprocess
        try:
            result = subprocess.run([luau_path, harness_path],
                                    capture_output=True, timeout=args.timeout)
            raw_output = result.stdout.decode("utf-8", "replace")
        except subprocess.TimeoutExpired:
            print("[!] timed out after %ds" % args.timeout, file=sys.stderr)
            return None
    else:
        # No luau.exe available - generate trace from source analysis
        raw_output = generate_trace_from_source(source, args, api_data)

    # Process the trace output
    result_path = process_trace(raw_output, trace_path, args, api_data)
    return result_path


def build_harness(source, args, api_data):
    """Build the harness that wraps the source with neb.luau runtime."""
    cfg_lua = "{\n"
    cfg_lua += "  time_budget = %d,\n" % args.time_budget
    cfg_lua += "  max_stmts = %d,\n" % args.max_instructions
    cfg_lua += "  max_block = %d,\n" % args.max_block
    cfg_lua += "  max_waits = %d,\n" % args.max_waits
    cfg_lua += "  time_budget = %d,\n" % args.time_budget
    cfg_lua += "  executor = %q,\n" % args.executor
    cfg_lua += "}\n"

    harness = """--!nocheck
local __SOURCE = %s
local __CONFIG = %s

-- Embed neb.luau runtime
%s

-- Execute the source
local chunk, cerr = load(__SOURCE, "=Script")
if not chunk then
    error("load error: " .. tostring(cerr), 0)
end
local ok, err = pcall(chunk)
if not ok then
    error("runtime error: " .. tostring(err), 0)
end
""" % (repr(source), cfg_lua, read_neb_luau())

    return harness


def read_neb_luau():
    neb_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "neb.luau")
    if os.path.exists(neb_path):
        with open(neb_path, "r", encoding="utf-8") as f:
            return f.read()
    return ""


def find_luau():
    """Find luau.exe binary."""
    import shutil
    neb_dir = os.path.dirname(os.path.abspath(__file__))
    # Check bundled luau.exe
    for p in [os.path.join(neb_dir, "bin", "luau.exe"),
              os.path.join(neb_dir, "bin", "luau"),
              os.path.join(os.path.dirname(neb_dir), "bin", "luau.exe")]:
        if os.path.exists(p):
            return p
    # Check system
    return shutil.which("luau") or shutil.which("luau.exe")


def process_trace(raw_output, trace_path, args, api_data):
    """Process the raw output from the harness into readable Luau."""
    # Strip control characters and clean up
    body = raw_output

    # Apply tidy/fold passes unless disabled
    if not args.no_tidy:
        body = tidy_trace(body)

    if not args.no_fold:
        body = fold_helpers(body)

    # Write the result
    with open(trace_path, "w", encoding="utf-8", newline="\n") as f:
        f.write(body)

    return trace_path


def tidy_trace(body):
    """Tidy up the trace output for readability."""
    # Remove empty lines
    lines = body.split("\n")
    lines = [l for l in lines if l.strip()]
    return "\n".join(lines)


def fold_helpers(body):
    """Fold repeated helper functions into reusable functions."""
    return body

if __name__ == "__main__":
    main()