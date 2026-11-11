from urllib.request import urlopen
from bs4 import BeautifulSoup


def extract_facility(url):
    with urlopen(url, timeout=15) as response:
        html = response.read().decode("utf-8")

    soup = BeautifulSoup(html, "html.parser")

    name = soup.find("h1").get_text(strip=True)
    details = soup.find("dl", class_="detail")

    address_label = details.find("dt", string="Address")
    address_value = address_label.find_next_sibling("dd")
    address_lines = list(address_value.stripped_strings)

    street = address_lines[0]

    city, state_zip = address_lines[1].rsplit(",", 1)
    state, zip_code = state_zip.strip().split(maxsplit=1)
    city = city.strip()

    care_label = details.find("dt", string="Care Offerings")
    care_value = care_label.find_next_sibling("dd")

    care_offerings = [
        badge.get_text(strip=True)
        for badge in care_value.find_all("span", class_="badge")
    ]

    return {
        "name": name,
        "street": street,
        "city": city,
        "state": state,
        "zip": zip_code,
        "care_offerings": care_offerings,
        "source_url": url,
    }


if __name__ == "__main__":
    url = "https://analyst-assessment-production.up.railway.app/communities/bellhaven-of-maplewood"

    facility = extract_facility(url)

    print(facility)