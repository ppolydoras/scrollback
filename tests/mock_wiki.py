"""A stand-in for the Wikipedia "On this day" endpoint, for offline tests.

Serves /{lang}/feed/onthisday/{type}/{MM}/{DD} and generated images under /img/.
Run: python3 tests/mock_wiki.py 9999
"""
from __future__ import annotations

import hashlib
import io
import json
import re
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

BASE = "http://127.0.0.1:{port}"


PLACES = {
    "Tombstone, Arizona": "City in Arizona, United States",
    "London": "Capital and largest city of the United Kingdom",
    "Somalia": "Country in the Horn of Africa",
    "Vienna": "Capital city of Austria",
    "New York City": "Most populous city in the United States",
    "Paris": "Capital and largest city of France",
    "Greece": "Country in Southeast Europe",
}

# (month, day, year, text, main title, description, extract, extra links, featured)
EVENTS = [
    (10, 26, 1881, "The Gunfight at the O.K. Corral takes place in Tombstone, Arizona.",
     "Gunfight at the O.K. Corral", "1881 gunfight in Tombstone, Arizona",
     "The Gunfight at the O.K. Corral was a thirty-second shootout between lawmen led by Virgil Earp and members of a loosely organized group of outlaws called the Cowboys. It took place at about 3:00 p.m. on Wednesday, October 26, 1881, in Tombstone, Arizona Territory. It is generally regarded as the most famous shootout in the history of the American Old West.",
     ["Tombstone, Arizona"], 1),
    (10, 26, 1863, "The Football Association is founded at a meeting in the Freemasons' Tavern in London.",
     "The Football Association", "Governing body of association football in England",
     "The Football Association is the governing body of association football in England. Formed in 1863, it is the oldest football association in the world and is responsible for overseeing all aspects of the amateur and professional game in its territory.",
     ["London"], 0),
    (10, 26, 1955, "Austria's parliament passes the Declaration of Neutrality, committing the country to permanent neutrality.",
     "Declaration of Neutrality", "1955 Austrian constitutional law",
     "The Declaration of Neutrality was a declaration by the Austrian Parliament that the country would be permanently neutral. It was enacted on 26 October 1955 as a constitutional act. The date has been celebrated as the Austrian National Day since 1965.",
     ["Vienna"], 1),
    (10, 26, 1958, "Pan American World Airways makes the first commercial flight of the Boeing 707, from New York City to Paris.",
     "Boeing 707", "American narrow-body jet airliner",
     "The Boeing 707 is an American long-range narrow-body airliner developed and produced by Boeing Commercial Airplanes. It was the first commercially successful jetliner of the jet age, and dominated passenger air transport in the 1960s.",
     ["New York City", "Paris"], 0),
    (10, 26, 1977, "Ali Maow Maalin, the last person to catch smallpox from natural infection, develops a rash in Somalia.",
     "Ali Maow Maalin", "Somali hospital cook, last natural smallpox case",
     "Ali Maow Maalin was a Somali hospital cook and health worker from Merca who was the last person known to have been infected with naturally occurring variola minor smallpox. He was diagnosed in late October 1977 and made a full recovery. Smallpox was declared eradicated in 1980.",
     ["Somalia", "Smallpox"], 1),
    (10, 26, 1940, "The North American P-51 Mustang makes its first flight.",
     "North American P-51 Mustang", "American WWII fighter aircraft",
     "The North American Aviation P-51 Mustang is an American long-range, single-seat fighter and fighter-bomber used during the Second World War and the Korean War. It was designed in 1940 for the British Purchasing Commission.",
     [], 0),
    (10, 27, 1904, "The first underground line of the New York City Subway opens.",
     "New York City Subway", "Rapid transit system in New York City",
     "The New York City Subway is a rapid transit system in New York City. The first underground line opened on October 27, 1904, almost 35 years after the opening of the first elevated line in the city.",
     ["New York City"], 1),
    (10, 27, 1961, "NASA launches the first Saturn I rocket in mission Saturn-Apollo 1.",
     "Saturn I", "American rocket of the 1960s",
     "The Saturn I was a rocket designed as the United States' first medium lift launch vehicle. Its first flight, on 27 October 1961, was a suborbital test of the first stage.",
     [], 0),
    (10, 25, 1415, "Hundred Years' War: Henry V of England defeats a larger French army at the Battle of Agincourt.",
     "Battle of Agincourt", "1415 battle of the Hundred Years' War",
     "The Battle of Agincourt was an English victory in the Hundred Years' War. It took place on 25 October 1415, near Azincourt in northern France. The English army was led by King Henry V, and much of the victory is attributed to English and Welsh longbowmen.",
     ["Henry V of England"], 1),
    (10, 25, 1854, "Crimean War: The Charge of the Light Brigade takes place during the Battle of Balaclava.",
     "Charge of the Light Brigade", "1854 British cavalry charge",
     "The Charge of the Light Brigade was a failed military action involving the British light cavalry led by Lord Cardigan against Russian forces during the Battle of Balaclava on 25 October 1854 in the Crimean War.",
     [], 0),
    (10, 24, 1648, "The Peace of Westphalia is signed, ending the Thirty Years' War and the Eighty Years' War.",
     "Peace of Westphalia", "1648 peace treaties",
     "The Peace of Westphalia is the collective name for two peace treaties signed in October 1648 in the Westphalian cities of Osnabrück and Münster. They ended the Thirty Years' War and brought peace to the Holy Roman Empire.",
     [], 1),
    (10, 24, 1945, "The United Nations Charter comes into effect and the United Nations is formally established.",
     "United Nations", "Intergovernmental organization",
     "The United Nations is a global intergovernmental organization established by the signing of the UN Charter on 26 June 1945 and its entry into force on 24 October 1945. Its stated purposes are to maintain international peace and security.",
     [], 1),
    (10, 28, 1886, "In New York Harbor, President Grover Cleveland dedicates the Statue of Liberty.",
     "Statue of Liberty", "Colossal sculpture in New York Harbor",
     "The Statue of Liberty is a colossal neoclassical sculpture on Liberty Island in New York Harbor. The copper statue, a gift to the United States from the people of France, was designed by the French sculptor Frédéric Auguste Bartholdi and dedicated on October 28, 1886.",
     ["New York City"], 1),
    (10, 28, 1940, "World War II: Italy invades Greece after Greece rejects an ultimatum, beginning the Greco-Italian War.",
     "Greco-Italian War", "1940 to 1941 conflict between Italy and Greece",
     "The Greco-Italian War took place between Italy and Greece from 28 October 1940 to 23 April 1941. It marked the beginning of the Balkans campaign of World War II. The Greek counter-offensive pushed the Italian forces back into Albania.",
     ["Greece"], 1),
    (10, 29, 1929, "Black Tuesday: The Wall Street Crash reaches its peak, ending the Roaring Twenties.",
     "Wall Street Crash of 1929", "Major American stock market crash",
     "The Wall Street Crash of 1929 was a major American stock market crash that occurred in the autumn of 1929. It started in September and ended late in October, when share prices on the New York Stock Exchange collapsed.",
     [], 1),
    (10, 29, 1969, "The first message is sent over ARPANET, the forerunner of the Internet.",
     "ARPANET", "Early packet-switching network",
     "The Advanced Research Projects Agency Network (ARPANET) was the first wide-area packet-switched network with distributed control and one of the first computer networks to implement the TCP/IP protocol suite.",
     [], 1),
    (10, 23, 1942, "World War II: The Second Battle of El Alamein begins in Egypt.",
     "Second Battle of El Alamein", "1942 battle of the Western Desert campaign",
     "The Second Battle of El Alamein took place near the Egyptian railway halt of El Alamein. The Allied victory marked the end of the Axis threat to Egypt, the Suez Canal and the Middle Eastern oil fields.",
     [], 0),
    (10, 22, 1797, "André-Jacques Garnerin makes the first recorded frameless parachute jump, from a balloon above Paris.",
     "André-Jacques Garnerin", "French balloonist and inventor of the frameless parachute",
     "André-Jacques Garnerin was a French balloonist and the inventor of the frameless parachute. On 22 October 1797 he made the first recorded descent with such a parachute, from a hydrogen balloon above the Parc Monceau in Paris.",
     ["Paris"], 0),
    (10, 21, 1805, "Napoleonic Wars: The Royal Navy defeats a combined French and Spanish fleet at the Battle of Trafalgar.",
     "Battle of Trafalgar", "1805 naval battle",
     "The Battle of Trafalgar was a naval engagement between the British Royal Navy and the combined fleets of the French and Spanish navies during the War of the Third Coalition. Admiral Lord Nelson was killed during the battle.",
     [], 1),
    (10, 20, 1973, "The Sydney Opera House is opened by Queen Elizabeth II.",
     "Sydney Opera House", "Performing arts centre in Sydney",
     "The Sydney Opera House is a multi-venue performing arts centre in Sydney, New South Wales, Australia. Designed by the Danish architect Jørn Utzon, it was formally opened on 20 October 1973.",
     [], 0),
    (10, 4, 1957, "The Soviet Union launches Sputnik 1, the first artificial satellite to orbit the Earth.",
     "Sputnik 1", "First artificial Earth satellite",
     "Sputnik 1 was the first artificial Earth satellite. It was launched into an elliptical low Earth orbit by the Soviet Union on 4 October 1957. It orbited for three weeks before its batteries died.",
     [], 1),
    (10, 14, 1066, "The Normans defeat the English at the Battle of Hastings.",
     "Battle of Hastings", "1066 battle of the Norman Conquest",
     "The Battle of Hastings was fought on 14 October 1066 between the Norman-French army of William, the Duke of Normandy, and an English army under the Anglo-Saxon King Harold Godwinson, beginning the Norman Conquest of England.",
     [], 1),
    (10, 31, 1517, "Martin Luther sends his Ninety-five Theses to the Archbishop of Mainz, an event later seen as the start of the Protestant Reformation.",
     "Ninety-five Theses", "1517 disputation by Martin Luther",
     "The Ninety-five Theses are a list of propositions for an academic disputation written in 1517 by Martin Luther. They started the Reformation, a schism in the Catholic Church which profoundly changed Europe.",
     [], 1),
    (9, 2, -31, "Final War of the Roman Republic: Octavian's forces defeat Mark Antony and Cleopatra at the Battle of Actium.",
     "Battle of Actium", "31 BC naval battle",
     "The Battle of Actium was a naval battle fought between Octavian's maritime fleet, led by Marcus Agrippa, and the combined fleets of both Mark Antony and Cleopatra. It was fought on 2 September 31 BC in the Ionian Sea, near the former Roman colony of Actium, Greece.",
     ["Greece"], 1),
    (3, 15, -44, "Julius Caesar is assassinated by a group of Roman senators.",
     "Assassination of Julius Caesar", "Killing of the Roman dictator in 44 BC",
     "Gaius Julius Caesar, the Roman dictator, was assassinated by a group of senators on the Ides of March, 15 March 44 BC, during a meeting of the Senate at the Curia of Pompey.",
     [], 1),
    (5, 29, 1453, "Ottoman forces under Sultan Mehmed II capture Constantinople, ending the Byzantine Empire.",
     "Fall of Constantinople", "1453 capture of the Byzantine capital",
     "The Fall of Constantinople was the capture of the capital of the Byzantine Empire by the Ottoman Empire. The city was captured on 29 May 1453 as part of the culmination of a 53-day siege which had begun on 6 April.",
     [], 1),
    (7, 20, 1969, "Apollo 11's lunar module Eagle lands on the Moon.",
     "Apollo 11", "First crewed Moon landing",
     "Apollo 11 was the spaceflight that landed the first humans, Americans Neil Armstrong and Buzz Aldrin, on the Moon on July 20, 1969. Michael Collins flew the command module Columbia alone in lunar orbit.",
     [], 1),
    (11, 9, 1989, "East German authorities open the border crossings in the Berlin Wall.",
     "Fall of the Berlin Wall", "1989 opening of the inner German border",
     "The fall of the Berlin Wall on 9 November 1989 was a pivotal event in world history which marked the destruction of the Berlin Wall and the figurative Iron Curtain.",
     [], 1),
    (6, 18, 1815, "Napoleonic Wars: The Battle of Waterloo ends Napoleon's rule as Emperor of the French.",
     "Battle of Waterloo", "1815 battle in present-day Belgium",
     "The Battle of Waterloo was fought on Sunday 18 June 1815, near Waterloo, then in the United Kingdom of the Netherlands, now in Belgium. A French army under Napoleon was defeated by two armies of the Seventh Coalition.",
     [], 1),
    (7, 14, 1789, "French Revolution: Citizens of Paris storm the Bastille.",
     "Storming of the Bastille", "1789 event of the French Revolution",
     "The Storming of the Bastille occurred in Paris on the afternoon of 14 July 1789, when revolutionary insurgents stormed and seized control of the medieval armoury, fortress and political prison known as the Bastille.",
     ["Paris"], 1),
    (4, 15, 1912, "The RMS Titanic sinks in the North Atlantic after striking an iceberg the previous night.",
     "Titanic", "British ocean liner that sank in 1912",
     "RMS Titanic was a British ocean liner that sank in the early hours of 15 April 1912 as a result of striking an iceberg on her maiden voyage from Southampton, England, to New York City.",
     [], 1),
    (9, 1, 1939, "World War II: Germany invades Poland, beginning the war in Europe.",
     "Invasion of Poland", "1939 German and Soviet attack on Poland",
     "The invasion of Poland was a joint attack on the Second Polish Republic by Nazi Germany, the Slovak Republic, and the Soviet Union, which marked the beginning of World War II.",
     [], 1),
]


def page_obj(port: int, title: str, description: str, extract: str, image: bool) -> dict:
    slug = title.replace(" ", "_")
    p = {
        "type": "standard",
        "title": slug,
        "titles": {"canonical": slug, "normalized": title, "display": title},
        "description": description,
        "extract": extract,
        "content_urls": {"desktop": {"page": f"https://en.wikipedia.org/wiki/{slug}"}},
    }
    if image:
        h = hashlib.md5(title.encode()).hexdigest()[:8]
        base = BASE.format(port=port)
        p["thumbnail"] = {"source": f"{base}/img/320px-{h}.jpg", "width": 320, "height": 213}
        p["originalimage"] = {"source": f"{base}/img/{h}.jpg", "width": 1200, "height": 800}
    return p


def payload(port: int, kind: str, month: int, day: int) -> dict:
    items_sel, items_ev = [], []
    for (m, d, year, text, title, desc, extract, extra, featured) in EVENTS:
        if (m, d) != (month, day):
            continue
        pages = []
        for name in extra:
            if name in PLACES:
                pages.append(page_obj(port, name, PLACES[name], f"{name} is a place. " * 4, True))
            else:
                pages.append(page_obj(port, name, "Related topic", f"{name} is related. " * 3, False))
        pages.insert(0 if not extra or extra[0] not in PLACES else len(pages), page_obj(port, title, desc, extract, True))
        item = {"text": text, "year": year, "pages": pages}
        items_ev.append(item)
        if featured:
            items_sel.append(item)
    out = {}
    if kind in ("selected", "all"):
        out["selected"] = items_sel
    if kind in ("events", "all"):
        out["events"] = items_ev
    if kind == "all":
        out["births"], out["deaths"], out["holidays"] = [], [], []
    return out


def make_image(name: str) -> bytes:
    from PIL import Image, ImageDraw
    h = int(hashlib.md5(name.encode()).hexdigest(), 16)
    w, ht = (800, 1100) if h % 3 == 0 else (1200, 800)
    base = ((h >> 0) % 90 + 60, (h >> 8) % 70 + 60, (h >> 16) % 60 + 50)
    img = Image.new("RGB", (w, ht), base)
    dr = ImageDraw.Draw(img)
    for i in range(14):
        x = (h >> (i * 3)) % w
        y = (h >> (i * 5)) % ht
        r = 80 + (h >> i) % 260
        shade = tuple(min(255, c + 10 + i * 6) for c in base)
        dr.ellipse([x - r, y - r, x + r, y + r], fill=shade)
    buf = io.BytesIO()
    img.save(buf, "JPEG", quality=80)
    return buf.getvalue()


def fake_story(user_text: str) -> str:
    """What a well-behaved model would return, built only from the source."""
    summary = re.search(r"Summary: (.+)", user_text).group(1)
    year = re.search(r"Year: (.+)", user_text).group(1)
    return json.dumps({
        "title": "Retold: " + " ".join(summary.split()[:6]),
        "story": f"In {year}, this happened: {summary} The source article gives more detail, "
                 "and this retelling keeps to what it says.",
        "quiz": {"question": "Which year is this post about?", "options": [year, "Another year", "A third year", "None"], "answer": 0},
        "topics": ["Politics and power"],
    })


class H(BaseHTTPRequestHandler):
    port = 9999

    def log_message(self, *a):
        pass

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers.get("Content-Length") or 0)))
        if self.path == "/v1/chat/completions":
            text = fake_story(body["messages"][-1]["content"])
            out = {"choices": [{"message": {"role": "assistant", "content": text}}]}
        elif self.path == "/v1/messages":
            if self.headers.get("x-api-key") != "test-key":
                self.send_response(401)
                self.end_headers()
                return
            out = {"content": [{"type": "text", "text": fake_story(body["messages"][-1]["content"])}]}
        else:
            self.send_response(404)
            self.end_headers()
            return
        data = json.dumps(out).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        m = re.fullmatch(r"/(\w+)/feed/onthisday/(\w+)/(\d\d)/(\d\d)", self.path)
        if m:
            body = json.dumps(payload(self.port, m.group(2), int(m.group(3)), int(m.group(4)))).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(body)
            return
        if self.path.startswith("/img/"):
            body = make_image(self.path)
            self.send_response(200)
            self.send_header("Content-Type", "image/jpeg")
            self.send_header("Access-Control-Allow-Origin", "*")
            self.end_headers()
            self.wfile.write(body)
            return
        self.send_response(404)
        self.end_headers()


if __name__ == "__main__":
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 9999
    H.port = port
    ThreadingHTTPServer(("127.0.0.1", port), H).serve_forever()
