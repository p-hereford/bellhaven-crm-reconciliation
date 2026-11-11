import json

with open("bellhaven_facilities.json", "r", encoding="utf-8") as file:
    facilities = json.load(file)

required_fields = [
    "name",
    "street",
    "city",
    "state",
    "zip",
    "care_offerings",
    "source_url",
]

errors = []
seen_urls = set()

for facility in facilities:
    name = facility.get("name", "UNKNOWN")

    for field in required_fields:
        if not facility.get(field):
            errors.append(f"{name}: Missing {field}")

    url = facility.get("source_url")

    if url in seen_urls:
        errors.append(f"{name}: Duplicate URL")

    seen_urls.add(url)

print("Total facilities:", len(facilities))
print("Unique URLs:", len(seen_urls))
print("Validation errors:", len(errors))

for error in errors:
    print("ERROR:", error)