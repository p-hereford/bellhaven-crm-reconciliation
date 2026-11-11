import json

from urllib.request import urlopen
from urllib.parse import urljoin
from bs4 import BeautifulSoup
from facility_probe import extract_facility

base_url = "https://analyst-assessment-production.up.railway.app"


def main():
    next_page = base_url + "/communities"

    communities = {}
    visited_pages = set()

    # Discover communities across all directory pages
    while next_page:

        if next_page in visited_pages:
            raise RuntimeError("Pagination loop detected")

        visited_pages.add(next_page)

        print("Scanning:", next_page)

        with urlopen(next_page, timeout=15) as response:
            html = response.read().decode("utf-8")

        soup = BeautifulSoup(html, "html.parser")

        for heading in soup.find_all("h3"):
            link = heading.find("a")

            if link:
                name = link.get_text(strip=True)
                url = urljoin(base_url, link["href"])

                communities[url] = name

        next_page = None

        for link in soup.find_all("a"):
            if link.get_text(strip=True).startswith("Next"):
                next_page = urljoin(base_url, link["href"])
                break

    # Discover additional homepage communities
    with urlopen(base_url, timeout=15) as response:
        homepage_html = response.read().decode("utf-8")

    homepage = BeautifulSoup(homepage_html, "html.parser")

    for link in homepage.select('a[href^="/communities/"]'):
        name = link.get_text(strip=True)
        url = urljoin(base_url, link["href"])

        communities[url] = name

    print("\nCommunities discovered:", len(communities))

    # Extract complete information from every facility
    facilities = []

    for url in communities:
        print("Extracting:", url)

        facility = extract_facility(url)
        facilities.append(facility)

    # Save the completed website snapshot
    with open("bellhaven_facilities.json", "w", encoding="utf-8") as file:
        json.dump(facilities, file, indent=4, ensure_ascii=False)

    print("\nExtraction complete.")
    print("Facilities saved:", len(facilities))
    print("File: bellhaven_facilities.json")


if __name__ == "__main__":
    main()