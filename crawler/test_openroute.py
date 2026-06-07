from openrouter import OpenRouter
import os

api_key = os.getenv("OPENROUTER_API_KEY")
if not api_key:
    raise SystemExit("Set OPENROUTER_API_KEY before running this script.")

client = OpenRouter(api_key=api_key)

SYSTEM_PROMPT = """ You are an expert Duel Masters card game rules engine parser.
Given a card's raw ability text lines (each starting with ■), you output a JSON object
with an "effects" array where each element is the structured representation of one ability.

Each effect object must have these exact fields:
{
  "ability_index": <int, 0-based position in the list>,
  "raw_text": "<exact input line>",
  "effect_type": "<one of: keyword|triggered|activated|static|replacement|cost_mod|spell>",
  "trigger_event": "<one of: on_enter_battle_zone|on_attack|on_break_shield|on_destroy|on_leave_battle_zone|start_of_turn|end_of_turn|on_summon|on_battle|on_block|on_draw|on_mana_charge|on_shield_trigger|none>",
  "trigger_condition": "<JSON object as a string, or null>",
  "effect_action": "<one of: draw|destroy|return_to_hand|search_deck|put_to_mana|summon_free|put_to_battle_zone|put_to_shield|add_to_hand|discard|tap|untap|power_modify|cannot_attack|cannot_be_blocked|cannot_be_destroyed|win_battle|break_shield|look_at_top|shuffle|cost_reduce|cost_increase|give_keyword|banish_to_abyss|move_zone|reveal|GR_summon|copy_effect|none>",
  "effect_target": "<JSON object as a string, or null>",
  "effect_value": "<JSON value as a string, or null>",
  "is_optional": <boolean>,
  "is_replacement": <boolean>,
  "active_in_phase": <array of strings, or ["any"]>,
  "active_in_zone": <array of strings, or ["battle_zone"]>,
  "parse_confidence": <float 0.0-1.0>
}

For unknown or complex effects use "none" for effect_action and lower confidence.
For trigger_condition, effect_target, and effect_value, return a valid JSON string
like "{"amount": 2}" or null. Do not return raw objects in those fields.

Targeting rules are important:
- If text says "a creature" or similar unrestricted wording, include legal own
  and opponent targets in effect_target scope rather than assuming opponent only.
- If a card moves from the Battle Zone to hand, use owner semantics when card text
  says "owner's hand".
- If the provided rules context conflicts with a shortcut or assumption, follow
  the rules context.
"""

prompt = """Card: Hannibal Zeta, the Charismatic Annihilator
Type: Creature

Abilities:
0. Face: Hannibal Zeta, the Charismatic Annihilator殲滅の英雄ハンニバルΖ (Creature)
   Ability: ■ Hell's Soul Recall 4 (Whenever this creature attacks or uses this ability, you may put 4 cards from your graveyard on the bottom of your deck in any order. If you do, use this ​ability.) Destroy one of your opponent's creatures.
1. Face: Hannibal Zeta, the Charismatic Annihilator殲滅の英雄ハンニバルΖ (Creature)
   Ability: ■ Double breaker (This creature breaks 2 shields.)



Rules context:
Exact rules likely relevant to card-effect parsing:
- [Rule 110.3] Abilities are broadly divided into 4 types.
- [Rule 110.4] Basically, spell abilities function during the resolution of the spell, and other abilities function only while the card is in the Battle Zone or Shield Zone. However, there are several exceptions as listed below.
- [Rule 110.5] The source of an effect is the card with the ability that generated it. The source of a triggered effect in a standby state is the card with the triggered ability.
- [Rule 112.2a] When paying a mana cost, you tap cards to fulfill all the identical civilizations indicated by the mana cost. When executing a card, you first tap one card in the Mana Zone per civilization to fulfill all the civilizations included in the mana cost. Furthermore, you tap cards in the Mana Zone to pay the remaining amount so that the total numerical value written on the mana symbols of the tapped cards equals the cost. [Example: Example: When paying the cost for a 7-mana multi-colored card with Fire, Darkness, and Nature, you must tap a card with Fire, a card with Darkness, and a card with Nature from the Mana Zone separately (1 card each), and then tap enough remaining cards to bring the total mana value to 7. Tapping one multi-colored card that has Fire, Darkness, and Nature does not fulfill all those civilizations; it is treated as providing only one of Fire, Darkness, or Nature.]
- [Rule 112.3a] S-Trigger is an ability where, when a shield is added to your hand due to a break, etc., you can execute the card immediately without paying its cost by showing it to your opponent and declaring its use. If multiple cards with S-Trigger are added from shields to your hand, you show and declare the cards you will use the S-Trigger ability for. Once all declarations are finished, execute those cards one by one.
- [Rule 112.3b] S-Back is an ability where, while a card with this ability is in a zone where it can be executed (usually the hand), when the specified card is added from a shield to your hand, you can immediately execute it without paying the cost by discarding that specified card and declaring its use.
- [Rule 112.3c] Ninja Strike is an ability that allows you to summon a creature without paying its cost if the specified number of cards in the Mana Zone is met, during the non-turn player's processing timing after a turn player's creature attacks, or during the turn player's processing timing after a non-turn player's creature blocks.
- [Rule 112.3e] G-Zero is an ability that allows you to summon a creature or cast a spell without paying its cost under specified conditions.
- [Rule 112.3g] Even when using other effects that state "you may execute without paying the cost," the acts of "summoning," "casting," or "generating" themselves are still taking place, so abilities that trigger from those acts will trigger.
- [Rule 113.6] When a shield is added to the hand due to a break or an effect, the player can declare the use of "S-Trigger", "G-Strike", and "S-Back". After all these declarations are finished, the shield moves to the hand.
- [Rule 509.5a] S-Trigger can be declared when a shield is broken and added to the hand. If there are multiple S-Triggers, they are declared simultaneously. You must reveal the declared card to the opponent.
- [Rule 509.5b] G-Strike can be declared when a shield is broken and added to the hand. If there are multiple G-Strikes, they are declared simultaneously. You must reveal the declared card to the opponent.
- [Rule 509.5c] S-Back can be declared by discarding the card added to the hand when a shield is broken. You must reveal the declared card to the opponent. The card discarded at this time is moved from the shield to the graveyard, but it is treated as a "card discarded from the hand."
- [Rule 603.3] Once an ability triggers, the effect temporarily enters a standby state, and from among all effects on standby at that moment, they are processed in order starting with the turn player's effects.
- [Rule 605.2c] Cards and abilities resolve in the order they are written. However, this sequence of actions may be altered by replacement effects, and the meaning of earlier instructions may change. In some cases, text written later on a card may modify the meaning of the preceding text.

Semantic rules retrieved for this specific card text:
- [Rule 818.1c] Category: Special Card. Some cards have Soul Icons written on their Hyper Soul X. While these cards are underneath a creature, the creature gains that Soul Icon in addition to the abilities of Hyper Soul X. [Example: Example: 《Evil Heart Taru, Calamity Soul of the Demon God》 has Tamashii (Soul) Soul in its Hyper Soul X. When this is underneath a creature, that creature possesses Tamashii Soul.]
- [Rule 703.4c] Category: State Based. A creature with power 0 or less is destroyed.
- [Rule 605.4] Category: Cost Payment. Zone: battle_zone. While looking at or revealing cards from the deck due to an effect that executes a card from the deck or puts a card from the deck into play, the cards in the deck being looked at (or revealed) — other than the card being executed (or put into play) — belong to the deck, but they are not affected by other effects. [Example: Example: If you look at the top 5 cards of your deck with the effect of 《Justice, Left God of the Holy Spirit》 and cast 《Energy Re:Light》 from among them, the 5 cards being looked at by the effect are not affected by the effect of 《Energy Re:Light》, so you draw the 6th and 7th cards from the top of the deck.] [Example: Example: If you reveal the top 4 cards of your deck with the "Final Revolution" of 《Dogiragon Nova, Azure Guardian》 and put 《Gaizekial, Demonic Emperor Connection》 from among them into the Battle Zone, the card shielded by the "EX Life" of the entering 《Gaizekial, Demonic Emperor Connection》 is the 5th card located beneath the 4 cards you are looking at.]
- [Rule 805.7] Category: Special Card. When a player specifies 1 card name due to an effect, that player can specify the card name of either face of the Psychic Creature, but cannot specify both.
- [Rule 804.3b] Category: Special Card. If an attacking God links, that attack is continued.
- [Rule 804.6] Category: Special Card. When a player specifies 1 card name due to an effect, that player can specify the card name of any 1 card included in the linked God, but cannot specify all of them.
- [Rule 301.3b] Category: General. To determine a creature's power, calculate from the base value printed in the bottom left of the card and apply any various continuous effects.
- [Rule 818.1b] Category: Special Card. While a card with Hyper Soul X is underneath a creature, that creature gains the abilities of Hyper Soul X. This is an exception to 200.3a.
Parse each ability into the JSON object format: {"effects": [...]}. Use the numbered index as ability_index and use only the Ability text as raw_text.
"""

response = client.chat.send(
    model="nvidia/nemotron-3-super-120b-a12b:free",
    messages=[
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": prompt}
    ],
)

print(response.choices[0].message.content)
