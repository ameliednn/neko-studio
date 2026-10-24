"""The optional search note: shots the reel should contain, written the way a brief
lists them ("baie vue du large, façades funaya, ruelles, bateau de pêche").

Each wish becomes its own search. Media banks are in English, so common French words
are translated with the small dictionary below; other words (place names, "funaya")
pass through unchanged. A media answers a wish when its description contains at least
half of the wish's words (any synonym counts)."""
from __future__ import annotations

import math
import re
from dataclasses import dataclass

from .verify import LINK_WORDS, normalize

# French (normalised, no accents) -> English synonyms; the first one is used to search.
WORDS = {
    "bateau de peche": "fishing boat", "bateaux de peche": "fishing boat",
    "coucher de soleil": "sunset", "lever de soleil": "sunrise", "ciel etoile": "starry|stars|milky",
    "vue aerienne": "aerial|drone", "vue du large": "from the sea|sea|offshore", "depuis la mer": "sea",
    "bord de mer": "seaside|coast|sea", "chute d eau": "waterfall|falls",
    "baie": "bay", "port": "harbor|harbour|port", "quai": "pier|quay|dock", "phare": "lighthouse",
    "plage": "beach", "falaise": "cliff", "falaises": "cliff|cliffs", "ile": "island", "iles": "island|islands",
    "ruelle": "alley|lane|street", "ruelles": "alley|alleys|lane|street", "rue": "street", "rues": "street",
    "facade": "facade|houses|house", "facades": "facade|houses|house", "maison": "house", "maisons": "houses|house",
    "toit": "roof", "toits": "roofs|roof", "bateau": "boat", "bateaux": "boats|boat", "peche": "fishing",
    "pecheur": "fisherman", "pecheurs": "fishermen|fisherman", "marche": "market", "marches": "market",
    "temple": "temple", "temples": "temple", "sanctuaire": "shrine", "pont": "bridge", "riviere": "river",
    "lac": "lake", "montagne": "mountain", "montagnes": "mountains|mountain", "foret": "forest",
    "cascade": "waterfall|falls", "jardin": "garden", "jardins": "garden|gardens", "chateau": "castle",
    "eglise": "church", "village": "village", "nuit": "night", "brume": "mist|fog|misty", "neige": "snow",
    "riziere": "rice", "rizieres": "rice|terraces", "panorama": "panorama|panoramic", "lanternes": "lanterns",
    "cerisiers": "cherry|blossom|sakura", "automne": "autumn|fall", "reflet": "reflection", "reflets": "reflection",
    "vue": "view", "mer": "sea", "ciel": "sky",
}


@dataclass
class Wish:
    text: str                      # as written in the note
    search: str                    # English search phrase
    groups: list[set[str]]         # each group: interchangeable words, one must appear
    opener: bool = False           # "ouverture : brume sur la baie" asks for the first shot

    def matches(self, description: str) -> bool:
        text = normalize(description)
        found = sum(any(f" {w} " in text for w in group) for group in self.groups)
        return found >= max(1, math.ceil(len(self.groups) / 2))


def asks_opener(text: str) -> bool:
    """True for a wish written as the opening shot: "ouverture : brume sur la baie"."""
    return normalize(text).startswith((" ouverture ", " premier plan ", " premiere image "))


def parse(note: str) -> list[Wish]:
    wishes = []
    for part in re.split(r"[,;\n•·]+", note or ""):
        rest = normalize(part)
        if not rest.strip():
            continue
        opener = rest.startswith((" ouverture ", " premier plan ", " premiere image "))
        for lead in (" ouverture ", " premier plan ", " premiere image "):
            rest = rest.replace(lead, " ", 1) if rest.startswith(lead) else rest
        groups: list[set[str]] = []
        search: list[str] = []
        for phrase in sorted(WORDS, key=len, reverse=True):  # longest phrases first
            if f" {phrase} " in rest:
                options = WORDS[phrase].split("|")
                groups.append(set(options))
                search.append(options[0])
                rest = rest.replace(f" {phrase} ", " ")
        for word in rest.split():  # names and words without translation pass through
            if word not in LINK_WORDS and len(word) > 1:
                groups.append({word})
                search.append(word)
        if groups:
            wishes.append(Wish(part.strip(), " ".join(search), groups, opener))
    return wishes[:8]
