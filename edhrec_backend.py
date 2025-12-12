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
import argparse


########################
# EDHRec Analyzer Class
########################

class EDHRecAnalyzer:
    def __init__(self):
        # Rate limiting
        self.last_scryfall_request = 0
        self.last_edhrec_request = 0
        self.SCRYFALL_MIN_DELAY = 0.12   # Scryfall: 50–100ms (safe: 120ms)
        self.EDHREC_MIN_DELAY    = 0.40  # EDHREC: ~0.4s

        # Build ID cache
        self.build_id = None

        # Caching paths
        self.cache_root = "./cache"
        self.deck_cache_dir = os.path.join(self.cache_root, "deck_cache")
        self.scryfall_cache_path = os.path.join(self.cache_root, "scryfall_cache.json")

        os.makedirs(self.cache_root, exist_ok=True)
        os.makedirs(self.deck_cache_dir, exist_ok=True)

        # Scryfall cache
        self.scryfall_cache = self.load_scryfall_cache()

    #################
    # Rate limiting
    #################

    def rate_limit_scryfall(self):
        now = time.time()
        elapsed = now - self.last_scryfall_request
        if elapsed < self.SCRYFALL_MIN_DELAY:
            time.sleep(self.SCRYFALL_MIN_DELAY - elapsed)
        self.last_scryfall_request = time.time()

    def rate_limit_edhrec(self):
        now = time.time()
        elapsed = now - self.last_edhrec_request
        if elapsed < self.EDHREC_MIN_DELAY:
            time.sleep(self.EDHREC_MIN_DELAY - elapsed)
        self.last_edhrec_request = time.time()

    #################
    # Format Helpers
    #################

    @staticmethod
    def format_commander_name(commander_name: str):
        non_alphas_regex = r"[^\w\s]"
        formatted_name = re.sub(non_alphas_regex, "", commander_name)
        formatted_name = formatted_name.lower()
        formatted_name = formatted_name.replace(" ", "-")
        return formatted_name

    ###########################
    # Build Manifest Fetching #
    ###########################

    def fetch_edhrec_build_id(self):
        """
        Fetches the EDHREC build ID by parsing the _buildManifest.js script path.
        Example script tag:
            /_next/static/<BUILD_ID>/_buildManifest.js
        """
        if self.build_id:
            return self.build_id

        self.rate_limit_edhrec()

        r = requests.get("https://edhrec.com")
        if r.status_code != 200:
            raise Exception("Failed to load EDHREC homepage to detect build ID")

        html = r.text

        marker = "_buildManifest.js"
        idx = html.find(marker)
        if idx == -1:
            raise Exception("Could not find _buildManifest.js reference in homepage.")

        prefix = html[:idx]

        static_marker = "/_next/static/"
        static_idx = prefix.rfind(static_marker)
        if static_idx == -1:
            raise Exception("Could not locate /_next/static/ in homepage.")

        start = static_idx + len(static_marker)
        end = prefix.find("/", start)
        build_id = prefix[start:end]

        if not build_id or len(build_id) < 5:
            raise Exception(f"Extracted invalid EDHREC build ID: '{build_id}'")

        self.build_id = build_id
        print(f"[INFO] EDHREC build ID detected: {build_id}")
        return build_id

    ##########################
    # Output Directory Cleanup
    ##########################

    def clean_output_directories(self, formatted_name: str):
        output_dir = os.path.join("./output", formatted_name, "edhrec-decklists")

        # Recreate output/<commander>/edhrec-decklists
        if os.path.exists(output_dir):
            for f in os.listdir(output_dir):
                try:
                    os.remove(os.path.join(output_dir, f))
                except Exception:
                    pass
        else:
            os.makedirs(output_dir, exist_ok=True)

        print(f"Output directory cleaned: {output_dir}")
        return output_dir

    ##############################
    # EDHREC Deck Table Functions
    ##############################

    def fetch_deck_table(self, commander_formatted: str):
        """
        commander_formatted: already formatted string (mr-orfeo-the-boulder)
        """
        url = f"https://json.edhrec.com/pages/decks/{commander_formatted}.json"

        self.rate_limit_edhrec()
        r = requests.get(url)

        if r.status_code != 200:
            raise Exception(f"Failed to fetch deck table: HTTP {r.status_code}")

        return r.json()

    #################
    # deck metadata #
    #################

    @staticmethod
    def save_run_metadata(output_directory, commander_name, recent, min_price, max_price, source_info):
        metadata_path = os.path.join(output_directory, "commander.txt")
        with open(metadata_path, "w") as f:
            f.write("Commander Run Metadata\n")
            f.write("======================\n\n")
            f.write(f"Timestamp: {datetime.now()}\n")
            f.write(f"Commander: {commander_name}\n")
            f.write(f"Max Decks: {recent}\n")
            f.write(f"Min Price: {min_price}\n")
            f.write(f"Max Price: {max_price}\n")
            f.write(f"Input Source: {source_info}\n")
        print(f"Saved run metadata -> {metadata_path}")

    ###################################
    # Deck Filtering (price + recency)
    ###################################

    @staticmethod
    def filter_deck_hashes(deck_table: dict, recent: int, min_price: float, max_price: float):
        entries = deck_table["table"]

        for e in entries:
            e["savedate_dt"] = datetime.strptime(e["savedate"], "%Y-%m-%d")

        sorted_entries = sorted(entries, key=lambda e: e["savedate_dt"], reverse=True)

        filtered = [
            e for e in sorted_entries
            if min_price <= e["price"] <= max_price
        ]

        limited = filtered[:recent]
        return [e["urlhash"] for e in limited]

    ##################################
    # Persistent Deck Cache Functions
    ##################################

    def load_deck_from_cache(self, deck_id):
        path = os.path.join(self.deck_cache_dir, deck_id + ".json")
        if os.path.exists(path):
            try:
                with open(path, "r") as f:
                    return json.load(f)
            except Exception:
                return None
        return None

    def save_deck_to_cache(self, deck_id, deck):
        path = os.path.join(self.deck_cache_dir, deck_id + ".json")
        with open(path, "w") as f:
            json.dump(deck, f, indent=2)

    ###############################
    # EDHREC Deck Fetching (API)
    ###############################

    def fetch_deck_by_hash(self, deck_id: str):
        # Check deck cache first
        cached = self.load_deck_from_cache(deck_id)
        if cached:
            return cached

        # Load build ID (fetch once)
        if not self.build_id:
            self.fetch_edhrec_build_id()

        self.rate_limit_edhrec()

        url = f"https://edhrec.com/_next/data/{self.build_id}/deckpreview/{deck_id}.json?deckId={deck_id}"
        r = requests.get(url)

        if r.status_code != 200:
            print(f"Failed to fetch deck {deck_id} - HTTP {r.status_code}")
            return None

        try:
            deck = r.json()["pageProps"]["data"]["deck"]
        except KeyError:
            print(f"Deck JSON format unexpected for {deck_id}")
            return None

        self.save_deck_to_cache(deck_id, deck)
        return deck

    #########################################
    # Parallel Deck Downloader (Rate-Aware)
    #########################################

    def fetch_decks_parallel(self, deck_hashes):
        all_decks = []
        if not deck_hashes:
            return all_decks

        max_workers = min(5, len(deck_hashes))

        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            futures = {
                executor.submit(self.fetch_deck_by_hash, deck_id): deck_id
                for deck_id in deck_hashes
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

    def load_scryfall_cache(self):
        if os.path.exists(self.scryfall_cache_path):
            try:
                with open(self.scryfall_cache_path, "r") as f:
                    return json.load(f)
            except Exception:
                return {}
        return {}

    def save_scryfall_cache(self):
        with open(self.scryfall_cache_path, "w") as f:
            json.dump(self.scryfall_cache, f, indent=2)

    def get_card_type(self, card_name: str):
        if card_name in self.scryfall_cache:
            return self.scryfall_cache[card_name]

        self.rate_limit_scryfall()
        url = f"https://api.scryfall.com/cards/named?exact={card_name}"
        r = requests.get(url)

        if r.status_code != 200:
            self.scryfall_cache[card_name] = "Unknown"
            self.save_scryfall_cache()
            return "Unknown"

        data = r.json()
        type_line = data.get("type_line", "Unknown")

        self.scryfall_cache[card_name] = type_line
        self.save_scryfall_cache()

        return type_line

    ###################################
    # Deck Processing (Card Counting)
    ###################################

    @staticmethod
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

    def group_cards_by_type(self, card_counts):
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
            type_line = self.get_card_type(card)

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

    @staticmethod
    def save_master_cardcount(card_counts, output_directory):
        sorted_cards = sorted(card_counts.items(), key=lambda x: x[1], reverse=True)

        with open(os.path.join(output_directory, "master_card_counts.txt"), "w") as f:
            for card, count in sorted_cards:
                f.write(f"{count}  {card}\n")

    @staticmethod
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


####################
# argument parsing #
####################

def parse_inputs():
    parser = argparse.ArgumentParser(
        description="EDHREC deck fetcher (CLI args optional)",
        add_help=True
    )

    parser.add_argument("--commander", type=str, help="Override commander name")
    parser.add_argument("--recent", type=int, help="Number of recent decks to use")
    parser.add_argument("--min-price", type=float, help="Minimum deck price")
    parser.add_argument("--max-price", type=float, help="Maximum deck price")

    args = parser.parse_args()

    if not any(vars(args).values()):
        print("\n--- Command Line Usage (optional) ---")
        print("python3 edhrec_decklists_json_to_txt.py --recent 20 --min-price 200 --max-price 450")
        print("Commander name is always read from commander.txt unless overridden.\n")
        print("No CLI arguments detected — falling back to interactive prompts.\n")

    if args.commander:
        commander_name = args.commander
    else:
        with open("commander.txt", "r") as f:
            commander_name = f.read().strip()

    if args.recent is not None:
        recent = args.recent
    else:
        recent = int(input("How many recent decks to use?: "))

    if args.min_price is not None:
        min_price = args.min_price
    else:
        min_price = float(input("Minimum deck price?: "))

    if args.max_price is not None:
        max_price = args.max_price
    else:
        max_price = float(input("Maximum deck price?: "))

    source_info = {
        "commander": "CLI" if args.commander else "file",
        "recent": "CLI" if args.recent is not None else "prompt",
        "min_price": "CLI" if args.min_price is not None else "prompt",
        "max_price": "CLI" if args.max_price is not None else "prompt"
    }

    return commander_name, recent, min_price, max_price, source_info


############################
# Module-level convenience #
############################

# Singleton analyzer instance for imports (web_app, etc.)
_analyzer = EDHRecAnalyzer()


def format_commander_name(commander_name: str):
    return _analyzer.format_commander_name(commander_name)


def fetch_edhrec_build_id():
    return _analyzer.fetch_edhrec_build_id()


def clean_output_directories(formatted_name: str):
    return _analyzer.clean_output_directories(formatted_name)


def fetch_deck_table(commander_formatted: str):
    return _analyzer.fetch_deck_table(commander_formatted)


def save_run_metadata(output_directory, commander_name, recent, min_price, max_price, source_info):
    return _analyzer.save_run_metadata(output_directory, commander_name, recent, min_price, max_price, source_info)


def filter_deck_hashes(deck_table: dict, recent: int, min_price: float, max_price: float):
    return _analyzer.filter_deck_hashes(deck_table, recent, min_price, max_price)


def fetch_decks_parallel(deck_hashes):
    return _analyzer.fetch_decks_parallel(deck_hashes)


def count_cards(all_decks):
    return _analyzer.count_cards(all_decks)


def group_cards_by_type(card_counts):
    return _analyzer.group_cards_by_type(card_counts)


def save_master_cardcount(card_counts, output_directory):
    return _analyzer.save_master_cardcount(card_counts, output_directory)


def save_cardtypes(type_groups, output_directory):
    return _analyzer.save_cardtypes(type_groups, output_directory)


########
# MAIN #
########

def main():
    root = Tk()
    root.attributes("-topmost", True)
    root.iconify()

    commander_name, recent, min_price, max_price, source_info = parse_inputs()

    formatted_name = _analyzer.format_commander_name(commander_name)

    # 0. Clean output directories for currently scripted commander
    output_directory = _analyzer.clean_output_directories(formatted_name)

    print("Commander is: ", commander_name)

    # 1. Get deck table
    deck_table = _analyzer.fetch_deck_table(formatted_name)

    # 2. Get filtered deck hashes
    deck_hashes = _analyzer.filter_deck_hashes(deck_table, recent, min_price, max_price)
    print(f"Using {len(deck_hashes)} deck hashes: {deck_hashes}")

    # 3. Fetch decklists (parallel)
    all_decks = _analyzer.fetch_decks_parallel(deck_hashes)

    # 4. Save metadata describing this run
    _analyzer.save_run_metadata(
        output_directory,
        commander_name,
        recent,
        min_price,
        max_price,
        source_info
    )

    # 5. Save decklists
    decklist_file = os.path.join(output_directory, formatted_name + "-decklists.txt")
    with open(decklist_file, "w") as f:
        for d in all_decks:
            f.write("\n".join(d))
            f.write("\n\n")

    print("Decklists saved to", decklist_file)

    # 6. Count cards
    card_counts = _analyzer.count_cards(all_decks)
    _analyzer.save_master_cardcount(card_counts, output_directory)

    # 7. Group by card type
    type_groups = _analyzer.group_cards_by_type(card_counts)
    _analyzer.save_cardtypes(type_groups, output_directory)

    print("Master card count and type lists saved in ./output/")

    root.destroy()


if __name__ == "__main__":
    main()
