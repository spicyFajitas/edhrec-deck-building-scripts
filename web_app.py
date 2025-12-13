import os
import io
import zipfile

import streamlit as st
import pandas as pd
import altair as alt

from edhrec_backend import (
    EDHRecAnalyzer,
)

analyzer = EDHRecAnalyzer()


###################################
# Streamlit UI Setup
###################################

st.set_page_config(page_title="EDHREC Deck Analyzer", layout="centered")
st.title("🧙‍♂️ EDHREC Deck Analyzer")
st.write("Fetch, analyze, and categorize EDHREC decklists automatically.")


###################################
# Session State Initialization
###################################

if "results_ready" not in st.session_state:
    st.session_state.results_ready = False

if "output_dir" not in st.session_state:
    st.session_state.output_dir = None

if "formatted_name" not in st.session_state:
    st.session_state.formatted_name = None

if "commander_name" not in st.session_state:
    st.session_state.commander_name = None

if "recent" not in st.session_state:
    st.session_state.recent = None

if "min_price" not in st.session_state:
    st.session_state.min_price = None

if "max_price" not in st.session_state:
    st.session_state.max_price = None

if "deck_hashes" not in st.session_state:
    st.session_state.deck_hashes = None

if "all_decks" not in st.session_state:
    st.session_state.all_decks = None

if "card_counts" not in st.session_state:
    st.session_state.card_counts = None

if "type_groups" not in st.session_state:
    st.session_state.type_groups = None


###################################
# Inputs
###################################

st.header("Commander Selection")

default_commander = ""
if os.path.exists("commander.txt"):
    with open("commander.txt", "r") as f:
        default_commander = f.read().strip()

commander_name = st.text_input("Commander Name", value=default_commander)

st.header("Deck Query Filters")
recent = st.number_input("How many recent decks to fetch?", min_value=1, max_value=200, value=20)
min_price = st.number_input("Minimum deck price", min_value=1.0, max_value=10000.0, value=1.0)
max_price = st.number_input("Maximum deck price", min_value=1.0, max_value=10000.0, value=100.0)

run_button = st.button("Fetch & Analyze Decklists")


###################################
# Run
###################################

if run_button:
    st.session_state.results_ready = False

    if not commander_name.strip():
        st.warning("Enter a commander name first.")
        st.stop()

    formatted_name = analyzer.format_commander_name(commander_name)

    st.session_state.commander_name = commander_name
    st.session_state.formatted_name = formatted_name
    st.session_state.recent = int(recent)
    st.session_state.min_price = float(min_price)
    st.session_state.max_price = float(max_price)

    st.subheader("Step 1 — Detect EDHREC Build ID")
    with st.spinner("Detecting build ID…"):
        try:
            build_id = analyzer.fetch_edhrec_build_id()
            st.success(f"Build ID: `{build_id}`")
        except Exception as e:
            st.error(str(e))
            st.stop()

    st.subheader("Step 2 — Fetch Deck Table")
    with st.spinner("Fetching deck table…"):
        deck_table = analyzer.fetch_deck_table(formatted_name)

    deck_hashes = analyzer.filter_deck_hashes(deck_table, int(recent), float(min_price), float(max_price))
    st.session_state.deck_hashes = deck_hashes

    if not deck_hashes:
        st.warning("No decks found for those filters.")
        st.stop()

    st.success(f"Found {len(deck_hashes)} decks.")

    st.subheader("Step 3 — Download Decklists")
    progress = st.progress(0)
    status = st.empty()

    all_decks = []
    total = len(deck_hashes)

    # use backend parallel fetch, but update progress in the UI by counting completed decks
    all_decks = []

    for completed, total, deck in analyzer.fetch_decks_with_progress(deck_hashes):
        if deck:
            all_decks.append(deck)

        progress.progress(completed / total)
        status.info(f"Downloaded {completed}/{total} decks")


    st.session_state.all_decks = all_decks

    st.success(f"Downloaded {len(all_decks)} decks.")

    st.subheader("Step 4 — Write Output Files")

    output_dir = analyzer.clean_output_directories(formatted_name)
    st.session_state.output_dir = output_dir

    metadata_header = analyzer.build_metadata_header(
        commander_name,
        int(recent),
        float(min_price),
        float(max_price),
        source_info={"streamlit-ui": True},
    )

    decklist_path = analyzer.save_decklists(all_decks, output_dir, formatted_name, metadata_header)
    st.success(f"Saved decklists: `{decklist_path}`")

    st.subheader("Step 5 — Count Cards")
    with st.spinner("Counting cards…"):
        card_counts = analyzer.count_cards(all_decks)

    st.session_state.card_counts = card_counts
    analyzer.save_master_cardcount(card_counts, output_dir, metadata_header)
    st.success("Saved master_card_counts.txt")

    st.subheader("Step 6 — Classify Cards by Type")
    type_progress = st.progress(0)
    type_status = st.empty()

    # manual progress loop for web UI
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

    items = list(card_counts.items())
    total_cards = len(items)

    for idx, (card, count) in enumerate(items, start=1):
        type_line = analyzer.get_card_type(card)

        matched = False
        for t in type_groups:
            if t != "Unknown" and t in type_line:
                type_groups[t][card] = count
                matched = True
                break
        if not matched:
            type_groups["Unknown"][card] = count

        if total_cards > 0:
            type_progress.progress(idx / total_cards)
        type_status.info(f"Classified {idx}/{total_cards} cards")

    st.session_state.type_groups = type_groups

    analyzer.save_cardtypes(type_groups, output_dir, metadata_header)
    st.success("Saved card type lists.")

    st.session_state.results_ready = True
    st.success("✅ Processing complete!")


###################################
# Results
###################################

if st.session_state.results_ready:
    output_dir = st.session_state.output_dir
    formatted_name = st.session_state.formatted_name
    card_counts = st.session_state.card_counts
    type_groups = st.session_state.type_groups

    st.header("Download & Explore Output Files")

    output_files = sorted(os.listdir(output_dir))
    if not output_files:
        st.warning("No output files found.")
        st.stop()

    file_data = {}
    for filename in output_files:
        path = os.path.join(output_dir, filename)
        with open(path, "rb") as f:
            file_data[filename] = f.read()

    st.subheader("Download ALL Output Files (ZIP)")
    zip_buffer = io.BytesIO()
    with zipfile.ZipFile(zip_buffer, "w", zipfile.ZIP_DEFLATED) as zipf:
        for filename, data in file_data.items():
            zipf.writestr(filename, data)

    st.download_button(
        label="📦 Download All as ZIP",
        data=zip_buffer.getvalue(),
        file_name=f"{formatted_name}_edhrec_output.zip",
        mime="application/zip"
    )

    # Multi-select file downloader
    st.subheader("Select files to download")

    selected_files = st.multiselect(
        "Choose one or more output files",
        options=output_files,
        default=[],
    )

    for filename in selected_files:
        st.download_button(
            label=f"⬇ Download {filename}",
            data=file_data[filename],
            file_name=filename,
            mime="text/plain"
        )

    st.subheader("Preview File Contents")
    preview_file = st.selectbox("Choose a file to preview:", options=["(none)"] + output_files, index=0)
    if preview_file != "(none)":
        try:
            st.code(file_data[preview_file].decode("utf-8"), language="text")
        except Exception:
            st.warning("Cannot display this file as text.")

    # Dashboard Visualization
    st.header("Card Analysis Dashboard")

    card_df = pd.DataFrame(
        [(card, count) for card, count in card_counts.items()],
        columns=["Card", "Count"]
    ).sort_values("Count", ascending=False)

    top_n = st.slider("Show top N cards", min_value=5, max_value=100, value=20)

    rows = len(card_df.head(top_n))
    row_height = 24          # pixels per card (20–30 is a good range)
    min_height = 200
    max_height = 1200

    dynamic_height = min(
        max(rows * row_height, min_height),
        max_height
    )

    chart = (
        alt.Chart(card_df.head(top_n))
        .mark_bar()
        .encode(
            x=alt.X("Count:Q", title="Frequency Across Decks"),
            y=alt.Y("Card:N", sort="-x", title="Card Name"),
            tooltip=["Card", "Count"]
        )
        .properties(
            width=700,
            height=dynamic_height
        )
    )


    st.altair_chart(chart, use_container_width=True)

    st.success("Dashboard and download tools ready!")

st.info("Ready when you are — enter your commander and press the button!")
