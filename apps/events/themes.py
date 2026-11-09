"""Visual themes for event categories, used to pick card colors and icons."""

# (key, label, blurb, keywords matched against a category's slug and name)
THEMES = (
    ("sports", "Soccer & Sports", "Leagues, pickup games and tournaments.",
     ("soccer", "football", "sport", "league", "basketball", "run", "fitness", "tennis", "match")),
    ("party", "Parties", "Birthdays, nights out and celebrations.",
     ("party", "parties", "celebration", "birthday", "music", "concert", "dance", "festival")),
    ("outdoor", "Picnics & Outdoors", "Picnics, hikes and days in the sun.",
     ("picnic", "outdoor", "hike", "park", "camp", "beach", "nature")),
    ("social", "Social Gatherings", "Meetups, dinners and game nights.",
     ("social", "meetup", "dinner", "food", "drink", "game", "network")),
    ("community", "Community Events", "Volunteering, markets and local causes.",
     ("community", "volunteer", "charity", "market", "local", "workshop", "class")),
    ("other", "Other Experiences", "Everything else worth showing up for.", ()),
)


def theme_for(category):
    if category is None:
        return "other"
    text = f"{category.slug} {category.name}".lower()
    for key, _label, _blurb, keywords in THEMES:
        if any(keyword in text for keyword in keywords):
            return key
    return "other"

# Built-in photos shown when an event has no image of its own (static/gatherplay/img/themes/).
THEME_PHOTOS = {
    "sports": "gatherplay/img/themes/sports.jpg",
    "outdoor": "gatherplay/img/themes/outdoor.jpg",
    "social": "gatherplay/img/themes/social.jpg",
    "party": "gatherplay/img/themes/party.jpg",
    "community": "gatherplay/img/themes/community.jpg",
    "other": "gatherplay/img/themes/other.jpg",
}
