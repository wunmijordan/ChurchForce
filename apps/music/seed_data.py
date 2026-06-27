MASTER_TRACKS_PER_GENRE = 100

_SEED_TRACKS_BY_GENRE = {
    # Curated seed set: Nigerian worship
    "nigerian_worship": [
        ("Way Maker", "Sinach"),
        ("Imela", "Nathaniel Bassey"),
        ("Yahweh Sabaoth", "Nathaniel Bassey"),
        ("Onise Iyanu", "Nathaniel Bassey"),
        ("Miracle Worker", "Glowreeyah Braimah"),
        ("You Are Great", "Steve Crown"),
        ("Mighty God", "Joe Praize"),
        ("Excess Love", "Mercy Chinwo"),
        ("Obinasom", "Mercy Chinwo"),
        ("Ima Mfo", "Dunsin Oyekan"),
        ("Open Up", "Dunsin Oyekan"),
        ("Fragrance to Fire", "Dunsin Oyekan"),
        ("Chants", "Tope Alabi"),
        ("Awa Gbe O Ga", "Tope Alabi"),
        ("Omemma", "Chinyere Udoma"),
        ("Nara", "Tim Godfrey ft. Travis Greene"),
        ("Onise Iyanu Re", "Mike Abdul"),
        ("Most High", "Nosa"),
        ("The Name of Jesus", "Ada Ehi"),
        ("Only You Jesus", "Moses Bliss"),
    ],
    # Curated seed set: Nigerian praise
    "nigerian_praise": [
        ("Ekwueme", "Prospa Ochimana"),
        ("Able God", "Chigozie Wisdom"),
        ("Baba Ese", "Sammie Okposo"),
        ("Kabio Osi", "Glowreeyah Braimah"),
        ("You No Dey Use Me Play", "Eben"),
        ("Victory", "Eben"),
        ("You Are Yahweh", "Steve Crown"),
        ("Testimony", "Ada Ehi"),
        ("Cheta", "Ada Ehi"),
        ("Miracle No Dey Tire Jesus", "Moses Bliss"),
        ("Bigger Everyday", "Moses Bliss"),
        ("You I Live For", "Moses Bliss"),
        ("Count Your Blessings", "Buchi"),
        ("Mma Mma", "Frank Edwards"),
        ("No One Like You", "Frank Edwards"),
        ("Under the Canopy", "Frank Edwards"),
        ("I Have a Father", "Ada"),
        ("Omemma", "Judikay"),
        ("Capable God", "Judikay"),
        ("Idinma", "Judikay"),
    ],
    # Curated seed set: other African gospel/worship
    "african_gospel": [
        ("Jerusalema", "Master KG ft. Nomcebo"),
        ("Nara Ekele", "MOGmusic"),
        ("Alpha and Omega", "Gaise Baba"),
        ("Wakati wa Mungu", "Eunice Njeri"),
        ("Mungu ni Mwema", "Christina Shusho"),
        ("Ninashukuru", "Rose Muhando"),
        ("Ndivhe Masiari", "Janet Manyowa"),
        ("Favour", "Joe Mettle"),
        ("Bo Noo Ni", "Joe Mettle"),
        ("Ye Obua Mi", "Diana Hamilton"),
        ("Mo Ne Yo", "Diana Hamilton"),
        ("Wa Hamba Nathi", "Benjamin Dube"),
        ("El Shaddai Adonai", "Joyous Celebration"),
        ("Nara", "Ntokozo Mbambo"),
        ("Kamba Kaawe", "Kirk Franklin (East Africa choir cover)"),
        ("Bwana Ni Mchungaji", "Reuben Kigame"),
        ("Baba Yetu", "African Children's Choir"),
        ("Tambira Jehova", "Zimbabwe Gospel Choir"),
        ("Ndikhokhele Bawo", "Lusanda Spiritual Group"),
        ("Ufanelwe", "Takie Ndou"),
    ],
    # Curated seed set: global worship
    "global_worship": [
        ("Oceans (Where Feet May Fail)", "Hillsong UNITED"),
        ("What A Beautiful Name", "Hillsong Worship"),
        ("King of Kings", "Hillsong Worship"),
        ("Cornerstone", "Hillsong Worship"),
        ("Mighty To Save", "Hillsong Worship"),
        ("No Longer Slaves", "Bethel Music"),
        ("Goodness of God", "Bethel Music"),
        ("Raise A Hallelujah", "Bethel Music"),
        ("Living Hope", "Phil Wickham"),
        ("This Is Amazing Grace", "Phil Wickham"),
        ("Great Are You Lord", "All Sons & Daughters"),
        ("How Great Is Our God", "Chris Tomlin"),
        ("Our God", "Chris Tomlin"),
        ("Holy Forever", "Chris Tomlin"),
        ("10,000 Reasons", "Matt Redman"),
        ("Blessed Be Your Name", "Matt Redman"),
        ("Build My Life", "Pat Barrett"),
        ("Reckless Love", "Cory Asbury"),
        ("Do It Again", "Elevation Worship"),
        ("Graves Into Gardens", "Elevation Worship"),
    ],
    # Curated seed set: global praise/contemporary
    "global_praise": [
        ("Jireh", "Elevation Worship / Maverick City"),
        ("Firm Foundation", "Maverick City Music"),
        ("Promises", "Maverick City Music"),
        ("Champion", "Bethel Music"),
        ("Praise", "Elevation Worship"),
        ("RATTLE!", "Elevation Worship"),
        ("See A Victory", "Elevation Worship"),
        ("Who You Say I Am", "Hillsong Worship"),
        ("Shout To The Lord", "Darlene Zschech"),
        ("The Blessing", "Kari Jobe / Cody Carnes"),
        ("Forever", "Kari Jobe"),
        ("Break Every Chain", "Tasha Cobbs Leonard"),
        ("For Your Glory", "Tasha Cobbs Leonard"),
        ("I Smile", "Kirk Franklin"),
        ("Love Theory", "Kirk Franklin"),
        ("Total Praise", "Richard Smallwood"),
        ("Here I Am To Worship", "Tim Hughes"),
        ("Trading My Sorrows", "Darrell Evans"),
        ("Hosanna (Praise Is Rising)", "Paul Baloche"),
        ("Days of Elijah", "Robin Mark"),
    ],
}

_GENERIC_ARTIST_POOLS = {
    "nigerian_worship": [
        "House of Revival NG",
        "City Altar Choir",
        "Lagos Worship Collective",
        "River of Mercy Ensemble",
    ],
    "nigerian_praise": [
        "Praise Explosion NG",
        "Victory Sound Naija",
        "Grace Pulse Choir",
        "Sound of Zion Lagos",
    ],
    "african_gospel": [
        "African Worship Voices",
        "Continental Praise Union",
        "Ubuntu Gospel Collective",
        "Eastwind Worship Team",
    ],
    "global_worship": [
        "Global Worship House",
        "Anthem Chapel Music",
        "Fire Altar Collective",
        "Nations Worship Team",
    ],
    "global_praise": [
        "Praise Movement International",
        "Kingdom Celebration Band",
        "Faith Anthem Project",
        "Sound of Jubilee",
    ],
}


def _expand_genre_tracks(genre_key, seed_tracks, target_count=MASTER_TRACKS_PER_GENRE):
    """
    Expand each genre to a fixed-size master pack while keeping seed entries first.
    """
    artist_pool = _GENERIC_ARTIST_POOLS.get(genre_key, ["ChurchForce Music Team"])
    seen = set()
    items = []

    for title, artist in seed_tracks:
        key = (title.strip(), artist.strip())
        if key in seen:
            continue
        seen.add(key)
        items.append((key[0], key[1]))

    idx = 1
    label = genre_key.replace("_", " ").title()
    while len(items) < target_count:
        title = f"{label} Anthem {idx:03d}"
        artist = artist_pool[(idx - 1) % len(artist_pool)]
        key = (title, artist)
        if key not in seen:
            seen.add(key)
            items.append(key)
        idx += 1

    return items


POPULAR_CHRISTIAN_TRACKS_BY_GENRE = {
    genre: _expand_genre_tracks(genre, tracks)
    for genre, tracks in _SEED_TRACKS_BY_GENRE.items()
}

MASTER_CATALOG_TRACK_COUNT = sum(
    len(tracks) for tracks in POPULAR_CHRISTIAN_TRACKS_BY_GENRE.values()
)
