from chunker import chunk_file

chunks = chunk_file("dummy_js.js")

print(f"Found {len(chunks)} chunks:\n")
for c in chunks:
    if c['type'] == "module_level":
        print(c['text'])
    if c['name'] == "Config": 
        print(c['text'])
    print(f"--- {c['type']} (lines {c['start_line']}-{c['end_line']}) ---")
    print(f"name: {c['name']}, parent: {c['parent']}")
    print()