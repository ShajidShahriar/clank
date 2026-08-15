from chunker import chunk_python_file

chunks = chunk_python_file("dummy_decorators.py")

print(f"Found {len(chunks)} chunks:\n")
for c in chunks:
    if c['type'] == "module_level":
        print(c['text'])
    print(f"--- {c['type']} (lines {c['start_line']}-{c['end_line']}) ---")
    print(f"name: {c['name']}, parent: {c['parent']}")
    print()