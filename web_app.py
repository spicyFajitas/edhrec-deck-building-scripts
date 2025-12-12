import os
import io
import zipfile

import streamlit as st
import pandas as pd
import altair as alt

from edhrec_backend import (
    format_commander_name,
    fetch_edhrec_build_id,
    fetch_deck_table,
    filter_deck_hashes,
    fetch_decks_parallel,
    count_cards,
    group_cards_by_type,
    save_master_cardcount,
    save_cardtypes,
    clean_output_directories,
    save_run_metadata,
)


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

if "card_counts" not in st.session_state:
    st.session_state.card_counts = None

if "type_groups" not in st.session_state:
    st.session_state.type_groups = None

if "output_dir" not in st.session_state:
    st.session_state.output_dir = None

if "all_decks" not in st.session_state:
    st.session_state.all_decks = None

if "deck_hashes" not in st.session_state:
    st.session_state.deck_hashes = None

if "commander_name" not in st.session_state:
    st.session_state.commander_name = None

if "formatted_name" not in st.session_state:
    st.session_state.formatted_name = None

if "recent" not in st.session_state:
    st.session_state.recent = None

if "min_price" not in st.session_state:
    st.session_state.min_price = None

if "max_price" not in st.session_state:
    st.session_state.max_price = None


###################################
# Cached Backend Wrappers
###################################

@st.cache_data(show_spinner=False)
def cached_fetch_deck_table(formatted_name: str):
    return fetch_deck_table(formatted_name)


@st.cache_data(show_spinner=False)
def cached_filter_decks(deck_table: dict, recent: int, min_price: float, max_price: float):
    return filter_deck_hashes(deck_table, recent, min_price, max_price)


@st.cache_data(show_spinner=False)
def cached_count_cards(all_decks):
    return count_cards(all_decks)


@st.cache_data(show_spinner=False)
def cached_group_types(card_counts):
    return group_cards_by_type(card_counts)


###################################
# Commander Selection
###################################

st.header("Commander Selection")

default_commander = ""
if os.path.exists("commander.txt"):
    with open("commander.txt", "r") as f:
        default_commander = f.read().strip()

commander_name = st.text_input("Commander Name", value=default_commander)

if not commander_name:
    st.warning("Enter a commander name to continue.")
    st.stop()

formatted_name = format_commander_name(commander_name)

###################################
# Deck Query Options
###################################

st.header("Deck Query Filters")

recent = st.number_input("How many recent decks to fetch?", min_value=1, max_value=200, value=20)
min_price = st.number_input("Minimum deck price", min_value=0.0, max_value=10000.0, value=0.0)
max_price = st.number_input("Maximum deck price", min_value=0.0, max_value=10000.0, value=500.0)

run_button = st.button("Fetch & Analyze Decklists")


###################################
# Main Execution Section
###################################

if run_button:
    # Reset previous results
    st.session_state.results_ready = False

    st.session_state.commander_name = commander_name
    st.session_state.formatted_name = formatted_name
    st.session_state.recent = int(recent)
    st.session_state.min_price = float(min_price)
    st.session_state.max_price = float(max_price)

    st.subheader("Step 1 — Fetching EDHREC Build ID")
    with st.spinner("Resolving EDHREC build ID…"):
        try:
            build_id = fetch_edhrec_build_id()
            st.success(f"Using EDHREC Build ID: `{build_id}`")
        except Exception as e:
            st.error(f"Failed to detect build ID: {e}")
            st.stop()

    st.subheader("Step 2 — Fetching Deck Table Metadata")
    with st.spinner("Downloading deck metadata…"):
        try:
            deck_table = cached_fetch_deck_table(formatted_name)
        except Exception as e:
            st.error(f"Failed to fetch deck table: {e}")
            st.stop()

    deck_hashes = cached_filter_decks(deck_table, st.session_state.recent, st.session_state.min_price, st.session_state.max_price)
    st.session_state.deck_hashes = deck_hashes

    if len(deck_hashes) == 0:
        st.warning("No decks found in that price range.")
        st.stop()

    st.success(f"Found {len(deck_hashes)} matching decks.")

    ###################################
    # Fetch Decks
    ###################################

    st.subheader("Step 3 — Downloading Decklists")

    with st.spinner("Downloading decklists…"):
        all_decks = fetch_decks_parallel(deck_hashes)

    st.session_state.all_decks = all_decks
    st.success(f"Downloaded {len(all_decks)} decks.")

    ###################################
    # Clean output directory before saving
    ###################################

    output_dir = clean_output_directories(formatted_name)
    st.session_state.output_dir = output_dir

    ###################################
    # Save decklists
    ###################################

    st.subheader("Saving Decklists")
    decklist_file = os.path.join(output_dir, f"{formatted_name}-decklists.txt")

    with open(decklist_file, "w") as f:
        for d in all_decks:
            f.write("\n".join(d))
            f.write("\n\n")

    st.success(f"Decklists saved to `{decklist_file}`")

    ###################################
    # Save Metadata
    ###################################

    save_run_metadata(
        output_dir,
        commander_name,
        st.session_state.recent,
        st.session_state.min_price,
        st.session_state.max_price,
        source_info={"streamlit-ui": True},
    )

    ###################################
    # Process Card Counts
    ###################################

    st.subheader("Step 4 — Counting Cards")

    card_counts = cached_count_cards(all_decks)
    st.session_state.card_counts = card_counts

    save_master_cardcount(card_counts, output_dir)
    st.success("Master card count saved.")

    ###################################
    # Card Type Grouping
    ###################################

    st.subheader("Step 5 — Classifying Cards by Type")

    with st.spinner("Classifying card types…"):
        type_groups = cached_group_types(card_counts)
        st.session_state.type_groups = type_groups
        save_cardtypes(type_groups, output_dir)

    st.success("Card type lists saved.")

    st.session_state.results_ready = True
    st.success("Processing complete!")


###################################
# Results / Dashboard / Downloads
###################################

if st.session_state.results_ready:
    output_dir = st.session_state.output_dir
    Commander = st.session_state.commander_name
    formatted_name = st.session_state.formatted_name
    card_counts = st.session_state.card_counts
    type_groups = st.session_state.type_groups

    st.header("Download & Explore Output Files")

    output_files = sorted(os.listdir(output_dir))

    if not output_files:
        st.warning("No output files were generated.")
    else:
        st.write(f"All output files generated in: `{output_dir}`")

        # Build dictionary of file contents
        file_data = {}
        for filename in output_files:
            file_path = os.path.join(output_dir, filename)
            with open(file_path, "rb") as f:
                file_data[filename] = f.read()

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

        # Preview file contents
        st.subheader("Preview File Contents")

        preview_file = st.selectbox(
            "Pick a file to preview:",
            options=["(none)"] + output_files,
            index=0
        )

        if preview_file != "(none)":
            try:
                st.code(file_data[preview_file].decode("utf-8"), language="text")
            except Exception:
                st.warning("This file cannot be displayed as text.")

        # ZIP download
        st.subheader("Download ALL Output Files")

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

        # Dashboard Visualization
        st.header("Card Analysis Dashboard")

        card_df = pd.DataFrame(
            [(card, count) for card, count in card_counts.items()],
            columns=["Card", "Count"]
        ).sort_values("Count", ascending=False)

        top_n = st.slider("Show top N cards", min_value=5, max_value=50, value=20)

        chart = (
            alt.Chart(card_df.head(top_n))
            .mark_bar()
            .encode(
                x=alt.X("Count:Q", title="Frequency Across Decks"),
                y=alt.Y("Card:N", sort="-x", title="Card Name"),
                tooltip=["Card", "Count"]
            )
            .properties(width=700, height=600)
        )

        st.altair_chart(chart, use_container_width=True)

        st.success("Dashboard and download tools ready!")


st.info("Ready when you are — enter your commander and press the button!")
