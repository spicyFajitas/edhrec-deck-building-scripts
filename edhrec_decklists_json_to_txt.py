import json
import os
from tkinter import filedialog, Tk
import requests
import re
import random
from datetime import datetime
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from tqdm import tqdm

# Global Variables
EDHREC_BUILD_ID = "pF41RFSK-suPYi-vAeaQ1"
LAST_SCRYFALL_REQUEST = 0
LAST_EDHREC_REQUEST = 0
SCRYFALL_MIN_DELAY = 0.12   # Scryfall requires 50–100ms between requests (safe: 120ms)
EDHREC_MIN_DELAY    = 0.40  # EDHREC safe throttle (0.4 seconds)

##########################
# Output Directory Cleanup
##########################

def clean_output_directories(formatted_name):
    output_dir = os.path.join("./output", formatted_name, "edhrec-decklists-compilation")
    cache_dir = "./cache"

    # Recreate output/<commander>/
    if os.path.exists(output_dir):
        for f in os.listdir(output_dir):
            try:
                os.remove(os.path.join(output_dir, f))
            except:
                pass
    else:
        os.makedirs(output_dir, exist_ok=True)

    # Optional: clean deck_cache/ but keep directory
    # Comment out these lines if you want persistent caching
    # if os.path.exists(cache_dir):
    #     for f in os.listdir(cache_dir):
    #         try:
    #             os.remove(os.path.join(cache_dir, f))
    #         except:
    #             pass
    # else:
    #     os.makedirs(cache_dir, exist_ok=True)

    print(f"Output directory cleaned: {output_dir}")
    return output_dir


def rate_limit_scryfall():
    global LAST_SCRYFALL_REQUEST
    now = time.time()
    elapsed = now - LAST_SCRYFALL_REQUEST
    if elapsed < SCRYFALL_MIN_DELAY:
        time.sleep(SCRYFALL_MIN_DELAY - elapsed)
    LAST_SCRYFALL_REQUEST = time.time()

def rate_limit_edhrec():
    global LAST_EDHREC_REQUEST
    now = time.time()
    elapsed = now - LAST_EDHREC_REQUEST
    if elapsed < EDHREC_MIN_DELAY:
        time.sleep(EDHREC_MIN_DELAY - elapsed)
    LAST_EDHREC_REQUEST = time.time()

#################
# Format Helpers
#################

def format_commander_name(commander_name:str):
    non_alphas_regex = r"[^\w\s]"
    formatted_name = re.sub(non_alphas_regex, "", commander_name)
    formatted_name = formatted_name.lower()
    formatted_name = formatted_name.replace(" ", "-")
    # print(f"Formatted name is {formatted_name}")
    return formatted_name


##############################
# EDHREC Deck Table Functions
##############################

def request_json_decks(commander_name:str):
    formatted_name = format_commander_name(commander_name)
    json_url = f"https://json.edhrec.com/pages/decks/{formatted_name}.json"

    rate_limit_edhrec()
    response = requests.get(json_url)

    if response.status_code == 200:
        return response.json()
    else:
        print(f"JSON request failed! Try different commander name")


def fetch_deck_table(commander_name: str):
    url = f"https://json.edhrec.com/pages/decks/{commander_name}.json"

    rate_limit_edhrec()
    r = requests.get(url)

    if r.status_code != 200:
        raise Exception(f"Failed to fetch deck table: HTTP {r.status_code}")

    return r.json()


###################################
# Deck Filtering (price + recency)
###################################

def filter_deck_hashes(deck_table: dict, max_decks: int, min_price: float, max_price: float):
    entries = deck_table["table"]

    for e in entries:
        e["savedate_dt"] = datetime.strptime(e["savedate"], "%Y-%m-%d")

    sorted_entries = sorted(entries, key=lambda e: e["savedate_dt"], reverse=True)

    filtered = [
        e for e in sorted_entries
        if min_price <= e["price"] <= max_price
    ]

    limited = filtered[:max_decks]
    return [e["urlhash"] for e in limited]


##################################
# Persistent Deck Cache Functions
##################################

DECK_CACHE_DIR = "cache/deck_cache"
os.makedirs(DECK_CACHE_DIR, exist_ok=True)

def load_deck_from_cache(deck_id):
    path = os.path.join(DECK_CACHE_DIR, deck_id + ".json")
    if os.path.exists(path):
        try:
            with open(path, "r") as f:
                return json.load(f)
        except:
            return None
    return None

def save_deck_to_cache(deck_id, deck):
    path = os.path.join(DECK_CACHE_DIR, deck_id + ".json")
    with open(path, "w") as f:
        json.dump(deck, f, indent=2)


###############################
# EDHREC Deck Fetching (API)
###############################

def fetch_deck_by_hash(deck_id: str):
    cached = load_deck_from_cache(deck_id)
    if cached:
        return cached

    rate_limit_edhrec()
    url = f"https://edhrec.com/_next/data/{EDHREC_BUILD_ID}/deckpreview/{deck_id}.json?deckId={deck_id}"
    r = requests.get(url)

    if r.status_code != 200:
        print(f"Failed to fetch deck {deck_id} - HTTP {r.status_code}")
        return None

    try:
        deck = r.json()["pageProps"]["data"]["deck"]
    except KeyError:
        print(f"Deck JSON format unexpected for {deck_id}")
        return None

    save_deck_to_cache(deck_id, deck)
    return deck


#########################################
# Parallel Deck Downloader (Rate-Aware)
#########################################

def fetch_decks_parallel(deck_hashes):
    all_decks = []
    max_workers = min(5, len(deck_hashes))

    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        futures = {
            executor.submit(fetch_deck_by_hash, deck_id): deck_id for deck_id in deck_hashes
        }

        for future in tqdm(as_completed(futures), total=len(futures), desc="Downloading decks"):
            deck_id = futures[future]
            try:
                deck = future.result()
                if deck:
                    all_decks.append(deck)
            except Exception as e:
                print(f"Error fetching deck {deck_id}: {e}")

    return all_decks


####################
# Scryfall Caching
####################

SCRYFALL_CACHE_PATH = "cache/scryfall_cache.json"

def load_scryfall_cache():
    if os.path.exists(SCRYFALL_CACHE_PATH):
        try:
            with open(SCRYFALL_CACHE_PATH, "r") as f:
                return json.load(f)
        except:
            return {}
    return {}

def save_scryfall_cache(cache):
    with open(SCRYFALL_CACHE_PATH, "w") as f:
        json.dump(cache, f, indent=2)

SCRYFALL_CACHE = load_scryfall_cache()


def get_card_type(card_name: str):
    if card_name in SCRYFALL_CACHE:
        return SCRYFALL_CACHE[card_name]

    rate_limit_scryfall()
    url = f"https://api.scryfall.com/cards/named?exact={card_name}"
    r = requests.get(url)

    if r.status_code != 200:
        SCRYFALL_CACHE[card_name] = "Unknown"
        save_scryfall_cache(SCRYFALL_CACHE)
        return "Unknown"

    data = r.json()
    type_line = data.get("type_line", "Unknown")

    SCRYFALL_CACHE[card_name] = type_line
    save_scryfall_cache(SCRYFALL_CACHE)

    return type_line


###################################
# Deck Processing (Card Counting)
###################################

def count_cards(all_decks):
    card_counts = {}

    for deck in all_decks:
        for line in deck:
            try:
                qty_str, card_name = line.split(" ", 1)
                qty = int(qty_str)
            except ValueError:
                continue

            card_counts[card_name] = card_counts.get(card_name, 0) + qty

    return card_counts


def group_cards_by_type(card_counts):
    type_groups = {
        "Creature": {},
        "Instant": {},
        "Sorcery": {},
        "Artifact": {},
        "Enchantment": {},
        "Planeswalker": {},
        "Battle": {},
        "Land": {},
        "Unknown": {}
    }

    for card, count in tqdm(card_counts.items(), desc="Classifying card types"):
        type_line = get_card_type(card)

        matched = False
        for t in type_groups:
            if t != "Unknown" and t in type_line:
                type_groups[t][card] = count
                matched = True
                break

        if not matched:
            type_groups["Unknown"][card] = count

    return type_groups


###########################################
# Saving Output (Master list + type lists)
###########################################

def save_master_cardcount(card_counts, output_directory):
    sorted_cards = sorted(card_counts.items(), key=lambda x: x[1], reverse=True)

    with open(os.path.join(output_directory, "master_card_counts.txt"), "w") as f:
        for card, count in sorted_cards:
            f.write(f"{count}  {card}\n")


def save_cardtypes(type_groups, output_directory):
    for type_name, cards in type_groups.items():
        if not cards:
            continue  # Only make file if populated

        filename = f"cards_{type_name.lower()}.txt"
        path = os.path.join(output_directory, filename)

        sorted_cards = sorted(cards.items(), key=lambda x: x[1], reverse=True)

        with open(path, "w") as f:
            for card, count in sorted_cards:
                f.write(f"{count}  {card}\n")


########
# MAIN #
########

def main():
    root = Tk()
    root.attributes("-topmost", True)
    root.iconify()

    with open('commander.txt', 'r') as file:
        commander_name = file.read()
        file.close()

    formatted_name = format_commander_name(commander_name)

    # 0. Clean output directories for currently scripted commander
    clean_output_directories(formatted_name)

    print("Commander is: ", commander_name)

    # 1. Get deck table
    deck_table = fetch_deck_table(formatted_name)

    # 2. Ask filtering options
    max_decks = int(input("How many recent decks to use?: "))
    min_price = float(input("Minimum deck price?: "))
    max_price = float(input("Maximum deck price?: "))

    # 3. Get filtered deck hashes
    deck_hashes = filter_deck_hashes(deck_table, max_decks, min_price, max_price)
    print(f"Using {len(deck_hashes)} deck hashes: {deck_hashes}")

    # 4. Fetch decklists (parallel)
    all_decks = fetch_decks_parallel(deck_hashes)

    # 5. Save decklists
    output_directory = os.path.join("./output", formatted_name, "edhrec-decklists-compilation")

    os.makedirs(output_directory, exist_ok=True)

    with open(os.path.join(output_directory, formatted_name + "-decklists.txt"), "w") as f:
        for d in all_decks:
            f.write("\n".join(d))
            f.write("\n\n")

    print("Decklists saved to output/" + formatted_name + "decklists.txt")

    # 6. Count cards
    card_counts = count_cards(all_decks)
    save_master_cardcount(card_counts, output_directory)

    # 7. Group by card type
    type_groups = group_cards_by_type(card_counts)
    save_cardtypes(type_groups, output_directory)

    print("Master card count and type lists saved in ./output/")

    root.destroy()


if __name__ == "__main__":
    main()
