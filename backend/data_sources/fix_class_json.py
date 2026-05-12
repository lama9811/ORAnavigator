import json

# Specify the input file (with gaps) and output file, using the correct subfolder
input_file = 'data_sources/career and educational resources.json'  # Matches your project structure
output_file = 'data_sources/career and educational resource.json'  # New compact file in the same subfolder

# Read and parse the original JSON
try:
    with open(input_file, 'r', encoding='utf-8') as f:
        data = json.load(f)
except FileNotFoundError:
    print(f"Error: The file '{input_file}' was not found. Check the subfolder and file name.")
    exit()
except json.JSONDecodeError as e:
    print(f"Error: The file '{input_file}' contains invalid JSON. Details: {e}")
    exit()

# Write the formatted JSON to a new file with indentation, removing 'u' prefixes
with open(output_file, 'w', encoding='utf-8') as f:
    json.dump(data, f, indent=2, ensure_ascii=False)  # Vertical format with 2-space indentation, no Unicode 'u'

print(f"Successfully compacted '{input_file}' to '{output_file}'. Check the new file!")