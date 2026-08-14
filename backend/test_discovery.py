from file_discovery import discover_files

files = discover_files("..")  
print(f"Found {len(files)} files:\n")
for f in sorted(files):
    print(f)
