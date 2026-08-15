from chunker import chunk_python_file

lines = "\n".join(f"    x{i} = {i}" for i in range(200))
source = f"def big_function():\n{lines}\n    return x0\n"

with open("dummy_oversized.py", "w") as f:
    f.write(source)

chunks = chunk_python_file("dummy_oversized.py")
print(f"Found {len(chunks)} chunks\n")
for c in chunks:
    print(f"--- {c['type']} {c['name']} (lines {c['start_line']}-{c['end_line']}) ---")
    print(f"  starts with: {c['text'][:40]!r}")
    print(f"  ends with:   {c['text'][-40:]!r}")
    print()
