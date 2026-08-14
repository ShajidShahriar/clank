from chunker import chunk_python_file

chunks = chunk_python_file("dummy.py")

print(f"Found {len(chunks)} chunks:\n")
for c in chunks:
    print(f"--- {c['type']} (lines {c['start_line']}-{c['end_line']}) ---")
    print(c)
    print()