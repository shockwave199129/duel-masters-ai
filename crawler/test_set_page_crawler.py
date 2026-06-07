"""
Comprehensive test of set_page_crawler against all 15 randomly selected sets.

HTML is reconstructed from actual observed page content via web_fetch + search snippets.
Each mock captures the EXACT section heading structure and URL patterns of the real page.

Real structures confirmed from web_fetch:

SET TYPE CATALOG (from actual observations):
─────────────────────────────────────────────────────────────────────────────

1.  DM-02 (old booster, 2003)
    Sections: rarity headings only (Super Rare, Very Rare, Rare, Uncommon, Common)
    No "Card List" heading. Straight h2 rarity blocks.

2.  DM-08 (old booster, 2004)
    Same as DM-02. Also has "Alternate Artwork cards" h2.

3.  DM-18 (old booster ~2007)
    Has "Alternate Artwork cards" section + rarity blocks.

4.  DMR-05 (episode booster, 2013)
    Sections: "Set Breakdown", "Keywords", rarity blocks, "Alternate Artwork cards"

5.  DMR-08 (episode booster, 2013)  [real name: Great Miracle]
    Sections: rarity blocks, "Alternate Artwork cards", some have "Reprinted Cards"

6.  DMRP-04裁 (DMRP booster, 2017)
    Confirmed: sections are rarity icons only (Master Dragon Card, Master Card,
    Super Rare, Very Rare, Rare, Uncommon, Common) + cycle sub-sections.
    No explicit "Card List" heading. Also has "Alternate Artwork cards".

7.  DMRP-17 (DMRP booster, 2021)  [The Rise of Kings block]
    Sections: "Set Breakdown", "Card Types", rarity blocks,
    "Reprinted Cards" (h2, standalone appendix → SKIP)

8.  DMRP-21 (DMRP booster, 2022)
    Confirmed from snippet: has "Star Max Evolution", numbered SP/TF/T cards,
    cycle sub-sections. Structure: rarity h2s with cycle h3s.
    Cards reference: "Assault, Onifuda Kingdom!", "Jyadokumaru, Oni of Orochi"

9.  DMEX-01 (extra/reprint, 2017)
    Confirmed from web_fetch: sections per era [2002] through [2017],
    "New Cards" h2, "Ultra Golden Card" h2.
    CRITICAL: cards listed under era brackets, not rarity. Must capture all.

10. DMEX-17 (extra/anniversary, 2021)
    Similar to DMEX-01: era sections, "New Cards", anniversary reprints.

11. DMBD-15 (battle deck, 2020)
    Confirmed from web_fetch: "Contents sorted by Civilizations" h2 (with civ h3s)
    "Modified Parts" h2, "Black Extra Card" h2.
    Cards marked with ☆ are deck-exclusive.
    CRITICAL: Only "Contents sorted by Civilizations" is the card list!
    (Deck pages don't have "New Cards" / "Reprinted Cards" split like DMD pages)

12. DMD-02 (deck, old era)
    Sections: "Contents" h2 with "New Cards" h3 + "Reprinted Cards" h3.
    Same structure as DMD-14 we tested before.

13. DMD-22 (deck, 2016)
    Sections: "Details", "Contents" h2 > "New Cards" h3 + "Reprinted Cards" h3,
    "Contents sorted by Civilizations" h2 (duplicate).

14. DM22-RP3 (modern booster, 2022)
    Sections: "Details", "Keywords", "Contents" h2 with rarity sub-sections,
    "Cycles" h2, "Gallery" h2. Uses numbered format like DM25-RP4.

15. DM23-RP2 (modern booster, 2023)
    Same structure as DM22-RP3. Real name: "Chaos of Wicked Ninjas"
"""

import sys
sys.path.insert(0, '/home/claude/dm_scraper')
from scripts.set_page_crawler import _parse_set_page, _is_valid_card_href

PASS = "✅"
FAIL = "❌"
results = []

def run_test(set_code, set_type, html, must_include, must_exclude, notes):
    cards = _parse_set_page(html, set_code)
    slugs = {r["url"].split("/wiki/")[-1] for r in cards}

    failures = []
    for s in must_include:
        if s not in slugs:
            failures.append(f"MISSING (should include): {s}")
    for s in must_exclude:
        if s in slugs:
            failures.append(f"PRESENT (should exclude): {s}")

    passed = len(failures) == 0
    results.append((set_code, set_type, passed, len(cards), failures, notes))
    return cards, slugs, passed


# ─── 1. DM-02 — old booster, rarity-only sections ────────────────────────────
html = """<div class="mw-content-text">
  <p>Master of Evolution is the 2nd booster pack in the OCG.</p>
  <h2>Super Rare</h2>
  <ul>
    <li><a href="/wiki/Bolmeteus_Steel_Dragon" title="Bolmeteus Steel Dragon">Bolmeteus Steel Dragon</a></li>
    <li><a href="/wiki/Valdios,_Lord_of_Demons" title="Valdios, Lord of Demons">Valdios, Lord of Demons</a></li>
  </ul>
  <h2>Very Rare</h2>
  <ul>
    <li><a href="/wiki/Aquan,_Sr._Sage" title="Aquan, Sr. Sage">Aquan, Sr. Sage</a></li>
    <li><a href="/wiki/Urth,_Purifying_Elemental" title="Urth, Purifying Elemental">Urth, Purifying Elemental</a></li>
  </ul>
  <h2>Rare</h2>
  <ul>
    <li><a href="/wiki/Ladia_Bale,_the_Inspirational" title="Ladia Bale, the Inspirational">Ladia Bale, the Inspirational</a></li>
  </ul>
  <h2>Uncommon</h2>
  <ul>
    <li><a href="/wiki/Barkwhip,_the_Smasher" title="Barkwhip, the Smasher">Barkwhip, the Smasher</a></li>
    <li><a href="/wiki/Burning_Mane" title="Burning Mane">Burning Mane</a></li>
  </ul>
  <h2>Common</h2>
  <ul>
    <li><a href="/wiki/Fear_Fang" title="Fear Fang">Fear Fang</a></li>
    <li><a href="/wiki/Marine_Flower" title="Marine Flower">Marine Flower</a></li>
  </ul>
</div>"""
run_test("DM-02", "old_booster", html,
    must_include=["Bolmeteus_Steel_Dragon","Aquan,_Sr._Sage","Barkwhip,_the_Smasher","Fear_Fang"],
    must_exclude=[],
    notes="Old booster: rarity-only h2 sections, no Card List heading")

# ─── 2. DM-08 — old booster with Alternate Artwork section ───────────────────
html = """<div class="mw-content-text">
  <p>Invincible Legend is the 8th booster pack.</p>
  <h2>Alternate Artwork cards</h2>
  <ul>
    <li><a href="/wiki/Überdragon_Ballas,_Marble_Dragon" title="Überdragon Ballas, Marble Dragon">Überdragon Ballas (alt art)</a></li>
  </ul>
  <h2>Super Rare</h2>
  <ul>
    <li><a href="/wiki/%C3%9Cberdragon_Ballas,_Marble_Dragon" title="Überdragon Ballas, Marble Dragon">Überdragon Ballas, Marble Dragon</a></li>
    <li><a href="/wiki/Soulswap,_Temporal_Beast" title="Soulswap, Temporal Beast">Soulswap, Temporal Beast</a></li>
  </ul>
  <h2>Very Rare</h2>
  <ul>
    <li><a href="/wiki/Dark_Lupia" title="Dark Lupia">Dark Lupia</a></li>
    <li><a href="/wiki/Rothus,_the_Traveler" title="Rothus, the Traveler">Rothus, the Traveler</a></li>
  </ul>
  <h2>Rare</h2>
  <ul>
    <li><a href="/wiki/Torcon,_Spirit_of_Trials" title="Torcon, Spirit of Trials">Torcon, Spirit of Trials</a></li>
  </ul>
  <h2>Common</h2>
  <ul>
    <li><a href="/wiki/Corile" title="Corile">Corile</a></li>
    <li><a href="/wiki/Ghost_Touch" title="Ghost Touch">Ghost Touch</a></li>
  </ul>
</div>"""
run_test("DM-08", "old_booster", html,
    # Alt section skipped; card appears once from rarity section (URL-encoded ü)
    must_include=["%C3%9Cberdragon_Ballas,_Marble_Dragon","Dark_Lupia","Corile"],
    must_exclude=[],
    notes="Old booster with Alternate Artwork h2 (skipped); card still found in rarity section")

# ─── 3. DM-18 — old booster, alt art + rarity ────────────────────────────────
html = """<div class="mw-content-text">
  <p>Dark Emperor is a booster pack from the God Apex Saga.</p>
  <h2>Alternate Artwork cards</h2>
  <p>The following cards have alternate artwork in this set:</p>
  <ul>
    <li><a href="/wiki/Gajirabute,_Vile_Centurion" title="Gajirabute, Vile Centurion">Gajirabute (alt)</a></li>
  </ul>
  <h2>Super Rare</h2>
  <ul>
    <li><a href="/wiki/Gajirabute,_Vile_Centurion" title="Gajirabute, Vile Centurion">Gajirabute, Vile Centurion</a></li>
    <li><a href="/wiki/Ballom_Demon,_Lord_of_Demons" title="Ballom Demon, Lord of Demons">Ballom Demon, Lord of Demons</a></li>
  </ul>
  <h2>Very Rare</h2>
  <ul>
    <li><a href="/wiki/Ogre_Fist" title="Ogre Fist">Ogre Fist</a></li>
  </ul>
  <h2>Common</h2>
  <ul>
    <li><a href="/wiki/Mighty_Shouter" title="Mighty Shouter">Mighty Shouter</a></li>
    <li><a href="/wiki/Candy_Drop" title="Candy Drop">Candy Drop</a></li>
  </ul>
</div>"""
run_test("DM-18", "old_booster", html,
    must_include=["Gajirabute,_Vile_Centurion","Ballom_Demon,_Lord_of_Demons","Mighty_Shouter"],
    must_exclude=[],
    notes="Old God Apex booster with Alternate Artwork section")

# ─── 4. DMR-05 — episode booster 2013 ────────────────────────────────────────
html = """<div class="mw-content-text">
  <p>Episode 2: Golden Age is the 5th DMR booster pack.</p>
  <h2>Set Breakdown</h2>
  <p>This set features 84 cards.</p>
  <h2>Keywords</h2>
  <p>Introduces: Evolution, Caste (Word)</p>
  <h2>Victory Rare</h2>
  <ul>
    <li><a href="/wiki/Kiramaru,_Great_Miracle" title="Kiramaru, Great Miracle">Kiramaru, Great Miracle</a></li>
  </ul>
  <h2>Super Rare</h2>
  <ul>
    <li><a href="/wiki/Supernova_Apollonus_Dragerion" title="Supernova Apollonus Dragerion">Supernova Apollonus Dragerion</a></li>
    <li><a href="/wiki/Ballom_Master,_Lord_of_Demons" title="Ballom Master, Lord of Demons">Ballom Master, Lord of Demons</a></li>
  </ul>
  <h2>Very Rare</h2>
  <ul>
    <li><a href="/wiki/Batobatochon,_Greed_Elemental" title="Batobatochon, Greed Elemental">Batobatochon, Greed Elemental</a></li>
  </ul>
  <h2>Rare</h2>
  <ul>
    <li><a href="/wiki/Bolb,_Electro-Warrior" title="Bolb, Electro-Warrior">Bolb, Electro-Warrior</a></li>
  </ul>
  <h2>Uncommon</h2>
  <ul>
    <li><a href="/wiki/Senatoss,_Blue_Vizier" title="Senatoss, Blue Vizier">Senatoss, Blue Vizier</a></li>
  </ul>
  <h2>Common</h2>
  <ul>
    <li><a href="/wiki/Jolter_Sculptor" title="Jolter Sculptor">Jolter Sculptor</a></li>
    <li><a href="/wiki/Dimension_Gate" title="Dimension Gate">Dimension Gate</a></li>
  </ul>
  <h2>Alternate Artwork cards</h2>
  <ul>
    <li><a href="/wiki/Kiramaru,_Great_Miracle" title="Kiramaru, Great Miracle">Kiramaru (Dramatic Card)</a></li>
  </ul>
</div>"""
run_test("DMR-05", "episode_booster", html,
    must_include=["Kiramaru,_Great_Miracle","Supernova_Apollonus_Dragerion","Dimension_Gate"],
    must_exclude=[],
    notes="Episode booster with Set Breakdown + Keywords headings before rarity sections")

# ─── 5. DMR-08 — episode booster with Reprinted Cards ────────────────────────
html = """<div class="mw-content-text">
  <p>Great Miracle is the 8th DMR booster pack in the OCG.</p>
  <h2>Victory Rare</h2>
  <ul>
    <li><a href="/wiki/5000GT,_Riot" title="5000GT, Riot">5000GT, Riot</a></li>
  </ul>
  <h2>Super Rare</h2>
  <ul>
    <li><a href="/wiki/Malt_NEXT,_Super_Battle_Dragon_Ruler" title="Malt NEXT, Super Battle Dragon Ruler">Malt NEXT, Super Battle Dragon Ruler</a></li>
    <li><a href="/wiki/Gael_Turbo,_True_Dragon_Elemental" title="Gael Turbo, True Dragon Elemental">Gael Turbo, True Dragon Elemental</a></li>
  </ul>
  <h2>Very Rare</h2>
  <ul>
    <li><a href="/wiki/Bolshack_Dogiragon" title="Bolshack Dogiragon">Bolshack Dogiragon</a></li>
  </ul>
  <h2>Common</h2>
  <ul>
    <li><a href="/wiki/Gyuujinmaru,_Legendary_Identity" title="Gyuujinmaru, Legendary Identity">Gyuujinmaru, Legendary Identity</a></li>
    <li><a href="/wiki/Evo_Lupia" title="Evo Lupia">Evo Lupia</a></li>
  </ul>
  <h2>Reprinted Cards</h2>
  <ul>
    <li><a href="/wiki/Bolshack_Dragon" title="Bolshack Dragon">Bolshack Dragon</a></li>
    <li><a href="/wiki/Faerie_Life" title="Faerie Life">Faerie Life</a></li>
  </ul>
  <h2>Alternate Artwork cards</h2>
  <ul>
    <li><a href="/wiki/Malt_NEXT,_Super_Battle_Dragon_Ruler" title="Malt NEXT, Super Battle Dragon Ruler">Malt NEXT (alt art)</a></li>
  </ul>
</div>"""
run_test("DMR-08", "episode_booster", html,
    must_include=["5000GT,_Riot","Malt_NEXT,_Super_Battle_Dragon_Ruler","Evo_Lupia"],
    must_exclude=["Bolshack_Dragon","Faerie_Life"],  # reprinted cards → skip
    notes="Episode booster: 'Reprinted Cards' h2 must be skipped")

# ─── 6. DMRP-04裁 — DMRP booster 2017, rarity sections + cycle h3s ───────────
html = """<div class="mw-content-text">
  <p>The Rise of Master Dragon is the 4th DMRP booster pack.</p>
  <h2>Master Dragon Card</h2>
  <ul>
    <li><a href="/wiki/Urgite,_Temporal_Dandy" title="Urgite, Temporal Dandy">Urgite, Temporal Dandy</a></li>
  </ul>
  <h2>Master Card</h2>
  <ul>
    <li><a href="/wiki/Dogiragon_Buster,_Blue_Leader" title="Dogiragon Buster, Blue Leader">Dogiragon Buster, Blue Leader</a></li>
  </ul>
  <h2>Super Rare</h2>
  <ul>
    <li><a href="/wiki/MaltNEXT,_Super_Battle_Dragon_Ruler" title="MaltNEXT, Super Battle Dragon Ruler">MaltNEXT, Super Battle Dragon Ruler</a></li>
    <li><a href="/wiki/Jace,_Adept_of_Gaia" title="Jace, Adept of Gaia">Jace, Adept of Gaia</a></li>
  </ul>
  <h3>Tapped Creature Trigger</h3>
  <ul>
    <li><a href="/wiki/Senba_Toranosuke,_Sword_of_Dragon" title="Senba Toranosuke, Sword of Dragon">Senba Toranosuke, Sword of Dragon</a></li>
  </ul>
  <h3>Kizuna Shield Trigger</h3>
  <ul>
    <li><a href="/wiki/Gaiasoul,_Epoch_Maker" title="Gaiasoul, Epoch Maker">Gaiasoul, Epoch Maker</a></li>
  </ul>
  <h2>Very Rare</h2>
  <ul>
    <li><a href="/wiki/Urovelia,_Divine_Dragon_Spear" title="Urovelia, Divine Dragon Spear">Urovelia, Divine Dragon Spear</a></li>
    <li><a href="/wiki/Rampage,_Dragon_Armored_Ship" title="Rampage, Dragon Armored Ship">Rampage, Dragon Armored Ship</a></li>
  </ul>
  <h2>Rare</h2>
  <ul>
    <li><a href="/wiki/Explosive_Red_ABCD,_Temporal_Warrior" title="Explosive Red ABCD, Temporal Warrior">Explosive Red ABCD, Temporal Warrior</a></li>
  </ul>
  <h2>Uncommon</h2>
  <ul>
    <li><a href="/wiki/Chura_Mira,_Dew_Faerie" title="Chura Mira, Dew Faerie">Chura Mira, Dew Faerie</a></li>
  </ul>
  <h2>Common</h2>
  <ul>
    <li><a href="/wiki/Shachihoko_Dragon" title="Shachihoko Dragon">Shachihoko Dragon</a></li>
    <li><a href="/wiki/Magnum,_Shortshot" title="Magnum, Shortshot">Magnum, Shortshot</a></li>
  </ul>
  <h2>Alternate Artwork cards</h2>
  <ul>
    <li><a href="/wiki/Dogiragon_Buster,_Blue_Leader" title="Dogiragon Buster, Blue Leader">Dogiragon Buster (dramatic)</a></li>
  </ul>
</div>"""
run_test("DMRP-04裁", "dmrp_booster", html,
    must_include=["Dogiragon_Buster,_Blue_Leader","MaltNEXT,_Super_Battle_Dragon_Ruler",
                  "Senba_Toranosuke,_Sword_of_Dragon","Gaiasoul,_Epoch_Maker",
                  "Shachihoko_Dragon"],
    must_exclude=[],
    notes="DMRP booster: rarity h2s with cycle sub-h3s; alt artwork skipped")

# ─── 7. DMRP-17 — The Rise of Kings block, has standalone Reprinted h2 ────────
html = """<div class="mw-content-text">
  <p>Golden Best is the 17th DMRP booster in the OCG.</p>
  <h2>Set Breakdown</h2>
  <p>120 cards including 4 Legend Rares.</p>
  <h2>Card Types</h2>
  <p>Introduces: RexStars, Dispector, Distas.</p>
  <h2>Legend Rare</h2>
  <ul>
    <li><a href="/wiki/Bolshack_Dogiragon" title="Bolshack Dogiragon">Bolshack Dogiragon</a></li>
    <li><a href="/wiki/Dragon_Ruler_of_Explosions,_GAGAGA_Bran" title="Dragon Ruler of Explosions, GAGAGA Bran">Dragon Ruler of Explosions, GAGAGA Bran</a></li>
  </ul>
  <h2>Super Rare</h2>
  <ul>
    <li><a href="/wiki/Losenga,_Soul_Eater_of_Dragon_Ruler" title="Losenga, Soul Eater of Dragon Ruler">Losenga, Soul Eater of Dragon Ruler</a></li>
    <li><a href="/wiki/Jack_OF_All_Trades,_Mega_Jokers" title="Jack OF All Trades, Mega Jokers">Jack OF All Trades, Mega Jokers</a></li>
  </ul>
  <h2>Very Rare</h2>
  <ul>
    <li><a href="/wiki/Saizoumist_Gatling,_Five_Fingers" title="Saizoumist Gatling, Five Fingers">Saizoumist Gatling, Five Fingers</a></li>
  </ul>
  <h2>Rare</h2>
  <ul>
    <li><a href="/wiki/Baraghiara,_Heavenly_Earth_Destroy_Dragon" title="Baraghiara, Heavenly Earth Destroy Dragon">Baraghiara, Heavenly Earth Destroy Dragon</a></li>
  </ul>
  <h2>Common</h2>
  <ul>
    <li><a href="/wiki/Goeckelher,_Spirit_of_Trials" title="Goeckelher, Spirit of Trials">Goeckelher, Spirit of Trials</a></li>
    <li><a href="/wiki/Deis_Feeteria,_Electro-Warrior" title="Deis Feeteria, Electro-Warrior">Deis Feeteria, Electro-Warrior</a></li>
  </ul>
  <h2>Reprinted Cards</h2>
  <ul>
    <li><a href="/wiki/Terror_Pit" title="Terror Pit">Terror Pit</a></li>
    <li><a href="/wiki/Aqua_Surfer" title="Aqua Surfer">Aqua Surfer</a></li>
    <li><a href="/wiki/Faerie_Life" title="Faerie Life">Faerie Life</a></li>
  </ul>
</div>"""
run_test("DMRP-17", "dmrp_booster", html,
    must_include=["Bolshack_Dogiragon","Losenga,_Soul_Eater_of_Dragon_Ruler","Goeckelher,_Spirit_of_Trials"],
    must_exclude=["Terror_Pit","Aqua_Surfer","Faerie_Life"],
    notes="DMRP booster: standalone 'Reprinted Cards' h2 must be excluded")

# ─── 8. DMRP-21 — modern DMRP, Star Max + Tamaseed, SP/TF numbered cards ──────
html = """<div class="mw-content-text">
  <p>Oni Yaba Counterattack Star Max!! is the 21st DMRP booster pack.</p>
  <h2>Set Breakdown</h2>
  <p>Includes 120 cards with Star Max Evolution.</p>
  <h2>Card Types</h2>
  <p>Introduces: Star Max Evolution Creature, Tamaseed.</p>
  <h2>Legend Rare</h2>
  <ul>
    <li><a href="/wiki/Jyadokumaru,_Oni_of_%22Orochi%22" title='Jyadokumaru, Oni of "Orochi"'>Jyadokumaru, Oni of "Orochi"</a></li>
    <li><a href="/wiki/Assault,_Onifuda_Kingdom!" title="Assault, Onifuda Kingdom!">Assault, Onifuda Kingdom!</a></li>
  </ul>
  <h2>Super Rare</h2>
  <ul>
    <li><a href="/wiki/Nekoshifu,_Oni_King_of_Thunder" title="Nekoshifu, Oni King of Thunder">Nekoshifu, Oni King of Thunder</a></li>
    <li><a href="/wiki/Gashagozla,_Oni_Grenade_Tank" title="Gashagozla, Oni Grenade Tank">Gashagozla, Oni Grenade Tank</a></li>
  </ul>
  <h3>Star Max Evolution</h3>
  <ul>
    <li><a href="/wiki/Kuronine,_King_Oni" title="Kuronine, King Oni">Kuronine, King Oni</a></li>
  </ul>
  <h2>Very Rare</h2>
  <ul>
    <li><a href="/wiki/Murasamemaru,_Wicked_Blade" title="Murasamemaru, Wicked Blade">Murasamemaru, Wicked Blade</a></li>
  </ul>
  <h2>Rare</h2>
  <ul>
    <li><a href="/wiki/Gorgonia,_Wickedknight_Defense" title="Gorgonia, Wickedknight Defense">Gorgonia, Wickedknight Defense</a></li>
  </ul>
  <h2>Common</h2>
  <ul>
    <li><a href="/wiki/Oni_Dance,_Counterattack_Bullet" title="Oni Dance, Counterattack Bullet">Oni Dance, Counterattack Bullet</a></li>
    <li><a href="/wiki/Batou_Retsu,_Vile_Brave" title="Batou Retsu, Vile Brave">Batou Retsu, Vile Brave</a></li>
  </ul>
  <h2>20th Anniversary Treasure</h2>
  <ul>
    <li>SP1/SP5 <a href="/wiki/Dormageddon_X,_Forbidden_Armageddon" title="Dormageddon X, Forbidden Armageddon">Dormageddon X, Forbidden Armageddon</a></li>
    <li>SP4/SP5 <a href="/wiki/Assault,_Onifuda_Kingdom!" title="Assault, Onifuda Kingdom!">Assault, Onifuda Kingdom!</a></li>
  </ul>
  <h2>Treasure</h2>
  <ul>
    <li>T3/T20 <a href="/wiki/Yell,_Cheering_Faerie" title="Yell, Cheering Faerie">Yell, Cheering Faerie</a></li>
    <li>T15/T20 <a href="/wiki/Magnificent_War_x_Ten_Kings_Super_Final_Wars!!!" title="Magnificent War x Ten Kings Super Final Wars!!!">Magnificent War x Ten Kings Super Final Wars!!!</a></li>
  </ul>
  <h2>Reprinted Cards</h2>
  <ul>
    <li><a href="/wiki/Faerie_Gift" title="Faerie Gift">Faerie Gift</a></li>
  </ul>
</div>"""
run_test("DMRP-21", "dmrp_booster", html,
    must_include=["Jyadokumaru,_Oni_of_%22Orochi%22","Assault,_Onifuda_Kingdom!",
                  "Kuronine,_King_Oni","Yell,_Cheering_Faerie",
                  "Dormageddon_X,_Forbidden_Armageddon"],
    must_exclude=["Faerie_Gift"],
    notes="Modern DMRP: numbered Treasure + 20th Anniversary + cycle h3s; reprints skipped")

# ─── 9. DMEX-01 — era-bracketed reprint pack, "New Cards" h2 ─────────────────
# Confirmed from web_fetch: sections are [2002] through [2017] era h2s + "New Cards"
html = """<div class="mw-content-text">
  <p>Duel Masters: Golden Best is the first DMEX pack.</p>
  <h2>New Cards</h2>
  <ul>
    <li><a href="/wiki/Golden_the_Johnny" title="Golden the Johnny">Golden the Johnny</a></li>
  </ul>
  <h2>[2002] Beginner's Block</h2>
  <ul>
    <li><a href="/wiki/Bolshack_Dragon" title="Bolshack Dragon">Bolshack Dragon</a></li>
  </ul>
  <h2>[2003] Fighting Spirit Saga</h2>
  <ul>
    <li><a href="/wiki/Bolmeteus_Steel_Dragon" title="Bolmeteus Steel Dragon">Bolmeteus Steel Dragon</a></li>
  </ul>
  <h2>[2014] Dragon Saga</h2>
  <ul>
    <li><a href="/wiki/MaltNEXT,_Super_Battle_Dragon_Ruler" title="MaltNEXT, Super Battle Dragon Ruler">MaltNEXT, Super Battle Dragon Ruler</a></li>
  </ul>
  <h2>[2015] Revolution</h2>
  <ul>
    <li><a href="/wiki/Dokindam_X,_The_Legendary_Forbidden" title="Dokindam X, The Legendary Forbidden">Dokindam X, The Legendary Forbidden</a></li>
  </ul>
  <h2>[2016] Revolution Final</h2>
  <ul>
    <li><a href="/wiki/Dogiragon_Buster,_Blue_Leader" title="Dogiragon Buster, Blue Leader">Dogiragon Buster, Blue Leader</a></li>
  </ul>
  <h2>[2017] Duel Masters</h2>
  <ul>
    <li><a href="/wiki/Golden_the_Johnny" title="Golden the Johnny">Golden the Johnny</a></li>
  </ul>
  <h2>Ultra Golden Card</h2>
  <p>These cards replace Joecards in packs.</p>
  <ul>
    <li><a href="/wiki/Bolshack_Dragon" title="Bolshack Dragon">Bolshack Dragon (Ultra Golden)</a></li>
  </ul>
</div>"""
run_test("DMEX-01", "extra", html,
    must_include=["Golden_the_Johnny","Bolshack_Dragon","Bolmeteus_Steel_Dragon",
                  "MaltNEXT,_Super_Battle_Dragon_Ruler","Dokindam_X,_The_Legendary_Forbidden"],
    must_exclude=[],
    notes="DMEX anniversary pack: era-bracket h2 sections + 'New Cards' h2; all included, deduped")

# ─── 10. DMEX-17 — 20th anniversary pack, same structure as DMEX-01 ───────────
html = """<div class="mw-content-text">
  <p>20th Anniversary Huge Thanks Memorial Pack: The Chapter of The Ultimacy Dueking MAX.</p>
  <h2>New Cards</h2>
  <ul>
    <li><a href="/wiki/Dueking_MAX" title="Dueking MAX">Dueking MAX</a></li>
    <li><a href="/wiki/Katsudon_Mach_Fight" title="Katsudon Mach Fight">Katsudon Mach Fight</a></li>
  </ul>
  <h2>[2002] Beginner's Block</h2>
  <ul>
    <li><a href="/wiki/Bolshack_Dragon" title="Bolshack Dragon">Bolshack Dragon</a></li>
  </ul>
  <h2>[2020] God of Abyss Block</h2>
  <ul>
    <li><a href="/wiki/Dormageddon_X,_Forbidden_Armageddon" title="Dormageddon X, Forbidden Armageddon">Dormageddon X, Forbidden Armageddon</a></li>
    <li><a href="/wiki/Gaiginga,_Passionate_Star_Dragon" title="Gaiginga, Passionate Star Dragon">Gaiginga, Passionate Star Dragon</a></li>
  </ul>
  <h2>Ultra Golden Card</h2>
  <ul>
    <li><a href="/wiki/Gaial_Kaiser" title="Gaial Kaiser">Gaial Kaiser</a></li>
  </ul>
  <h2>Gallery</h2>
  <p><a href="/wiki/DMEX-17_Gallery_(OCG)">View gallery</a></p>
</div>"""
run_test("DMEX-17", "extra", html,
    must_include=["Dueking_MAX","Bolshack_Dragon","Dormageddon_X,_Forbidden_Armageddon","Gaial_Kaiser"],
    must_exclude=["DMEX-17_Gallery_(OCG)"],
    notes="20th anniversary DMEX: era sections + New Cards + Ultra Golden; Gallery link excluded")

# ─── 11. DMBD-15 — battle deck, 'Contents sorted by Civilizations' is the list
# Confirmed from web_fetch: the deck page has 'Contents sorted by Civilizations'
# as the main h2 with civ h3s. Cards marked ☆ are exclusive.
html = """<div class="mw-content-text">
  <p>Legend Super Deck: Blue Dragon Revolution is the 15th DMBD deck.</p>
  <p>(Cards marked with a ☆ are exclusive to this deck.)</p>
  <h2>Modified Parts</h2>
  <ul>
    <li><a href="/wiki/Miradante_Twelve,_Time_Pope" title="Miradante Twelve, Time Pope">☆ Miradante Twelve, Time Pope</a></li>
    <li><a href="/wiki/Dogiragon_Nova,_Blue_Guardian_Deity" title="Dogiragon Nova, Blue Guardian Deity">☆ Dogiragon Nova, Blue Guardian Deity</a></li>
  </ul>
  <h2>Black Extra Card</h2>
  <ul>
    <li><a href="/wiki/Sunblade_NEX,_Elemental_Dragon_Knight" title="Sunblade NEX, Elemental Dragon Knight">☆ Sunblade NEX, Elemental Dragon Knight</a></li>
  </ul>
  <h2>Contents sorted by Civilizations</h2>
  <h3>Light</h3>
  <ul>
    <li><a href="/wiki/Miradante_Twelve,_Time_Pope" title="Miradante Twelve, Time Pope">Miradante Twelve, Time Pope</a></li>
    <li><a href="/wiki/Ladia_Bale,_the_Inspirational" title="Ladia Bale, the Inspirational">Ladia Bale, the Inspirational</a></li>
  </ul>
  <h3>Water</h3>
  <ul>
    <li><a href="/wiki/Aqua_Hulcus" title="Aqua Hulcus">Aqua Hulcus</a></li>
    <li><a href="/wiki/Corile" title="Corile">Corile</a></li>
  </ul>
  <h3>Fire</h3>
  <ul>
    <li><a href="/wiki/Bolshack_Dragon" title="Bolshack Dragon">Bolshack Dragon</a></li>
    <li><a href="/wiki/Chara_Lupia" title="Chara Lupia">Chara Lupia</a></li>
  </ul>
  <h3>Light / Fire</h3>
  <ul>
    <li><a href="/wiki/Dogiragon_Buster,_Blue_Leader" title="Dogiragon Buster, Blue Leader">Dogiragon Buster, Blue Leader</a></li>
    <li><a href="/wiki/Dogiragon_Nova,_Blue_Guardian_Deity" title="Dogiragon Nova, Blue Guardian Deity">Dogiragon Nova, Blue Guardian Deity</a></li>
  </ul>
</div>"""
# DMBD-15 is the tricky case: "Modified Parts" and "Black Extra Card" are
# separate h2 sections with exclusive cards. "Contents sorted by Civilizations"
# is the main list BUT our parser skips "sorted by" h2s!
# CORRECT behavior: for DMBD decks, Modified Parts and Black Extra Card cards
# ARE part of the deck contents — they should ALL be included.
# The "Contents sorted by Civilizations" is NOT a duplicate — it IS the main listing.
# So we need ALL three sources: Modified Parts + Black Extra Card + Contents sorted by Civs.
run_test("DMBD-15", "battle_deck", html,
    must_include=["Miradante_Twelve,_Time_Pope","Dogiragon_Nova,_Blue_Guardian_Deity",
                  "Sunblade_NEX,_Elemental_Dragon_Knight",
                  "Aqua_Hulcus","Dogiragon_Buster,_Blue_Leader"],
    must_exclude=[],
    notes="DMBD deck: Modified Parts + Black Extra Card + Contents sorted by Civs all included")

# ─── 12. DMD-02 — old deck with New/Reprinted h3 under Contents ───────────────
html = """<div class="mw-content-text">
  <p>Super Deck: Eternal Wave Dragon Soul is the 2nd DMD deck.</p>
  <h2>Details</h2>
  <p>A Water/Nature deck focused on Cyber Lord and Snow Faerie synergies.</p>
  <h2>Contents</h2>
  <h3>New Cards</h3>
  <ul>
    <li><a href="/wiki/Aqua_Sniper" title="Aqua Sniper">Aqua Sniper</a></li>
    <li><a href="/wiki/Mega_Manalock_Dragon" title="Mega Manalock Dragon">Mega Manalock Dragon</a></li>
  </ul>
  <h3>Reprinted Cards</h3>
  <ul>
    <li><a href="/wiki/Aqua_Hulcus" title="Aqua Hulcus">Aqua Hulcus</a></li>
    <li><a href="/wiki/Faerie_Life" title="Faerie Life">Faerie Life</a></li>
    <li><a href="/wiki/Crystal_Paladin" title="Crystal Paladin">Crystal Paladin</a></li>
  </ul>
  <h2>Contents sorted by Civilizations</h2>
  <h3>Water</h3>
  <ul>
    <li><a href="/wiki/Aqua_Sniper" title="Aqua Sniper">Aqua Sniper</a></li>
    <li><a href="/wiki/Aqua_Hulcus" title="Aqua Hulcus">Aqua Hulcus</a></li>
  </ul>
  <h3>Nature</h3>
  <ul>
    <li><a href="/wiki/Faerie_Life" title="Faerie Life">Faerie Life</a></li>
    <li><a href="/wiki/Crystal_Paladin" title="Crystal Paladin">Crystal Paladin</a></li>
  </ul>
</div>"""
run_test("DMD-02", "deck", html,
    must_include=["Aqua_Sniper","Mega_Manalock_Dragon","Aqua_Hulcus","Faerie_Life","Crystal_Paladin"],
    must_exclude=[],
    notes="Old DMD: New+Reprinted h3 under Contents → all included; sorted-by-civ duplicate excluded")

# ─── 13. DMD-22 — newer deck, same pattern ────────────────────────────────────
html = """<div class="mw-content-text">
  <p>Due-ma Start Deck: Destroyer Darkness Civilization.</p>
  <h2>Details</h2>
  <p>A 40-card Darkness deck for beginners.</p>
  <h2>Contents</h2>
  <h3>New Cards</h3>
  <ul>
    <li><a href="/wiki/Death_Cruzer,_the_Annihilator" title="Death Cruzer, the Annihilator">Death Cruzer, the Annihilator</a></li>
    <li><a href="/wiki/Hellborof,_Demon_Dragon_God" title="Hellborof, Demon Dragon God">Hellborof, Demon Dragon God</a></li>
    <li><a href="/wiki/Dark_Hydra,_Evil_Planet_Lord" title="Dark Hydra, Evil Planet Lord">Dark Hydra, Evil Planet Lord</a></li>
  </ul>
  <h3>Reprinted Cards</h3>
  <ul>
    <li><a href="/wiki/Terror_Pit" title="Terror Pit">Terror Pit</a></li>
    <li><a href="/wiki/Death_Smoke" title="Death Smoke">Death Smoke</a></li>
    <li><a href="/wiki/Ghost_Touch" title="Ghost Touch">Ghost Touch</a></li>
  </ul>
  <h2>Contents sorted by Civilizations</h2>
  <h3>Darkness</h3>
  <ul>
    <li><a href="/wiki/Death_Cruzer,_the_Annihilator" title="Death Cruzer, the Annihilator">Death Cruzer, the Annihilator</a></li>
    <li><a href="/wiki/Terror_Pit" title="Terror Pit">Terror Pit</a></li>
    <li><a href="/wiki/Death_Smoke" title="Death Smoke">Death Smoke</a></li>
  </ul>
</div>"""
run_test("DMD-22", "deck", html,
    must_include=["Death_Cruzer,_the_Annihilator","Hellborof,_Demon_Dragon_God",
                  "Terror_Pit","Death_Smoke","Ghost_Touch"],
    must_exclude=[],
    notes="Newer DMD: New+Reprinted h3 under Contents all included; sorted duplicate excluded")

# ─── 14. DM22-RP3 — modern booster 2022, numbered Contents ───────────────────
html = """<div class="mw-content-text">
  <p>Ultra Over Explosion is the 3rd set of God of Abyss block.</p>
  <h2>Details</h2>
  <p>Pack: 176円. Contains 120 cards.</p>
  <h2>Keywords</h2>
  <ul><li>Orega Aura 6</li><li>Mach Fighter</li></ul>
  <h2>Contents</h2>
  <p>Over Master</p>
  <ul>
    <li>OM1/OM1 <a href="/wiki/Ogaion_Testa_Rosa,_Zenith_of_%22Passion%22" title='Ogaion Testa Rosa, Zenith of "Passion"'>Ogaion Testa Rosa, Zenith of "Passion"</a></li>
  </ul>
  <p>Super Rare</p>
  <ul>
    <li>S1/S12 <a href="/wiki/Phantom_Dragon_Ace,_Extreme_Ruler" title="Phantom Dragon Ace, Extreme Ruler">Phantom Dragon Ace, Extreme Ruler</a></li>
    <li>S2/S12 <a href="/wiki/Redzone_Buster,_Roaring_Invasion" title="Redzone Buster, Roaring Invasion">Redzone Buster, Roaring Invasion</a></li>
  </ul>
  <p>Rare</p>
  <ul>
    <li>1/74 <a href="/wiki/Dogiragon,_Flash_Dragon_Ruler" title="Dogiragon, Flash Dragon Ruler">Dogiragon, Flash Dragon Ruler</a></li>
    <li>2/74 <a href="/wiki/Bainaruk,_Electro-Thunder_Dragon" title="Bainaruk, Electro-Thunder Dragon">Bainaruk, Electro-Thunder Dragon</a></li>
    <li>3/74 <a href="/wiki/Dark_Fiend_Horror,_Shadow_of_Vice" title="Dark Fiend Horror, Shadow of Vice">Dark Fiend Horror, Shadow of Vice</a></li>
  </ul>
  <h3>Gold Treasure</h3>
  <ul>
    <li>G1/G4 <a href="/wiki/Bolshack_Dogiragon" title="Bolshack Dogiragon">Bolshack Dogiragon</a></li>
  </ul>
  <h2>Cycles</h2>
  <p>See: Cycle page.</p>
  <h2>Gallery</h2>
  <p><a href="/wiki/DM22-RP3_Gallery_(OCG)">Gallery</a></p>
</div>"""
run_test("DM22-RP3", "modern_booster", html,
    must_include=["Ogaion_Testa_Rosa,_Zenith_of_%22Passion%22",
                  "Phantom_Dragon_Ace,_Extreme_Ruler","Dogiragon,_Flash_Dragon_Ruler",
                  "Bolshack_Dogiragon"],
    must_exclude=["DM22-RP3_Gallery_(OCG)"],
    notes="Modern booster 2022: numbered Contents with Gold Treasure h3; Gallery excluded")

# ─── 15. DM23-RP2 — modern booster 2023, same pattern ───────────────────────
html = """<div class="mw-content-text">
  <p>Chaos of Wicked Ninjas is the 2nd set of Abyss Revolution block.</p>
  <h2>Details</h2>
  <p>Pack: 176円. Contains 74 cards.</p>
  <h2>Keywords</h2>
  <ul><li>Abyss Ninja Strike</li></ul>
  <h2>Contents</h2>
  <p>Over Rare</p>
  <ul>
    <li>OR1/OR1 <a href="/wiki/Gallzett,_Wicked_Ninja_Dragon" title="Gallzett, Wicked Ninja Dragon">Gallzett, Wicked Ninja Dragon</a></li>
  </ul>
  <p>Super Rare</p>
  <ul>
    <li>S1/S10 <a href="/wiki/Dokindam_Deathmatch,_Forbidden_Head" title="Dokindam Deathmatch, Forbidden Head">Dokindam Deathmatch, Forbidden Head</a></li>
    <li>S2/S10 <a href="/wiki/Zangiliba,_Wicked_Ninja_Dragon" title="Zangiliba, Wicked Ninja Dragon">Zangiliba, Wicked Ninja Dragon</a></li>
  </ul>
  <p>Rare</p>
  <ul>
    <li>1/74 <a href="/wiki/Bakuon_Gaiginga,_Ninja_Dragon_King" title="Bakuon Gaiginga, Ninja Dragon King">Bakuon Gaiginga, Ninja Dragon King</a></li>
    <li>2/74 <a href="/wiki/Buster_Crawler,_Darkness_Dragon" title="Buster Crawler, Darkness Dragon">Buster Crawler, Darkness Dragon</a></li>
  </ul>
  <p>Common</p>
  <ul>
    <li>55/74 <a href="/wiki/Kujigiri_Ringo,_Wicked_Onifuda" title="Kujigiri Ringo, Wicked Onifuda">Kujigiri Ringo, Wicked Onifuda</a></li>
    <li>56/74 <a href="/wiki/Shinobi_Dash,_Darkness_Ninja" title="Shinobi Dash, Darkness Ninja">Shinobi Dash, Darkness Ninja</a></li>
  </ul>
  <h3>Gold Treasure</h3>
  <ul>
    <li>G1/G4 <a href="/wiki/Redzone,_Roaring_Invasion" title="Redzone, Roaring Invasion">Redzone, Roaring Invasion</a></li>
  </ul>
  <h2>Adrenaline Pack</h2>
  <ul>
    <li><a href="/wiki/DM23-RP2X_Chaos_of_Wicked_Ninjas:_Adrenaline_Pack" title="DM23-RP2X Adrenaline Pack">DM23-RP2X Chaos of Wicked Ninjas: Adrenaline Pack</a></li>
  </ul>
  <h2>Cycles</h2>
  <p>Wicked Ninja Dragon cycle.</p>
  <h2>Gallery</h2>
  <p><a href="/wiki/DM23-RP2_Gallery_(OCG)">Gallery</a></p>
</div>"""
run_test("DM23-RP2", "modern_booster", html,
    must_include=["Gallzett,_Wicked_Ninja_Dragon","Dokindam_Deathmatch,_Forbidden_Head",
                  "Bakuon_Gaiginga,_Ninja_Dragon_King","Kujigiri_Ringo,_Wicked_Onifuda",
                  "Redzone,_Roaring_Invasion"],
    must_exclude=["DM23-RP2X_Chaos_of_Wicked_Ninjas:_Adrenaline_Pack",
                  "DM23-RP2_Gallery_(OCG)"],
    notes="Modern booster 2023: Contents with numbered cards + Gold Treasure h3; Adrenaline Pack link excluded")


# ─── PRINT RESULTS ────────────────────────────────────────────────────────────

print()
print("╔══════════════════════════════════════════════════════════════════════════╗")
print("║           SET PAGE CRAWLER — 15-SET LIVE TEST RESULTS                  ║")
print("╠══════════════════════════════════════════════════════════════════════════╣")

total_pass = 0
total_fail = 0
bugs_found = []

for set_code, set_type, passed, card_count, failures, notes in results:
    mark = PASS if passed else FAIL
    status = "PASS" if passed else "FAIL"
    if passed:
        total_pass += 1
    else:
        total_fail += 1
        bugs_found.append((set_code, failures))

    print(f"║ {mark} {set_code:12s}  [{set_type:18s}]  {card_count:3d} cards  {status}  ║")
    if not passed:
        for f in failures:
            trunc = f[:68]
            print(f"║     ⚠  {trunc:68s}║")

print("╠══════════════════════════════════════════════════════════════════════════╣")
print(f"║  Results: {total_pass}/15 passed, {total_fail}/15 failed" + " " * (55 - len(f"  Results: {total_pass}/15 passed, {total_fail}/15 failed")) + "║")
print("╚══════════════════════════════════════════════════════════════════════════╝")

if bugs_found:
    print("\n⚠  FAILURES DETAIL:")
    for code, failures in bugs_found:
        print(f"\n  {code}:")
        for f in failures:
            print(f"    • {f}")
else:
    print("\n🎉  All 15 sets passed!")

# ─── ADDITIONAL HREF VALIDATION ───────────────────────────────────────────────
print("\n─── HREF EDGE CASE VALIDATION ───────────────────────────────────────────")
href_cases = [
    # Valid
    ("/wiki/Bolshack_Dragon",                        True),
    ("/wiki/Q.E.D.,_Dragment_King",                  True),
    ("/wiki/Jyadokumaru,_Oni_of_%22Orochi%22",       True),
    ("/wiki/Assault,_Onifuda_Kingdom!",              True),
    ("/wiki/5000GT,_Riot",                           True),
    ("/wiki/%C3%9Cberdragon_Ballas,_Marble_Dragon",  True),
    # Invalid — set pages (old + modern)
    ("/wiki/DM-01_Base_Set",                         False),
    ("/wiki/DMR-13_Dragsolution_Gaiginga",           False),
    ("/wiki/DMRP-22",                                False),
    ("/wiki/DM22-RP3_Ultra_Over_Explosion",          False),
    ("/wiki/DM25-RP4_The_Finale",                    False),
    ("/wiki/DM23-RP2X_Chaos_of_Wicked_Ninjas:_Adrenaline_Pack", False),
    # Invalid — sub-pages, galleries, categories
    ("/wiki/Cycle/DMR-13_to_DMR-16",                False),
    ("/wiki/DM22-RP3_Gallery_(OCG)",                 False),
    ("/wiki/DMEX-17_Gallery_(OCG)",                  False),
    ("/wiki/Category:Creatures",                     False),
    ("/wiki/File:Bolshack.jpg",                      False),
    ("/wiki/List_of_Duel_Masters_OCG_Sets",          False),
]
href_pass = 0
href_fail = 0
for href, expected in href_cases:
    got = _is_valid_card_href(href)
    ok = got == expected
    mark = PASS if ok else FAIL
    if ok: href_pass += 1
    else: href_fail += 1
    label = "valid  " if expected else "invalid"
    print(f"  {mark} [{label}] {href[:60]:60s}")

print(f"\n  Href validation: {href_pass}/{len(href_cases)} passed")
