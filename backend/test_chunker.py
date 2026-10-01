from chunker import chunk_file

chunks = chunk_file("dummy_class_body.py")

print(f"Found {len(chunks)} chunks:\n")
for c in chunks:
    if c['kind'] == "module_level":
        print(c['text'])
    if c['symbol'] == "Config": 
        print(c['text'])
    if c['kind'] == "class_overview": 
        print(c['text'])
        
    print(f"--- {c['kind']} (lines {c['start_line']}-{c['end_line']}) ---")
    print(f"name: {c['symbol']}, parent: {c['parent']}")
    print()