import os
import io
import zipfile

import streamlit as st
import pandas as pd
import altair as alt

from edhrec_backend import EDHRecAnalyzer

analyzer = EDHRecAnalyzer()

###################################
# Streamlit UI Setup
###################################

st.set_page_config(page_title="EDHRec Deck Builder Tool", layout="centered")
st.title("🧙‍♂️ EDHRec Deck Builder Tool")
st.write("Fetch, analyze, and categorize EDHREC decklists automatically.")
st.write(
    "This tool aggregates data from EDHRec for a given commander and shows the commonly used cards in a deck by count of how many decks the card is in."
)

###################################
# Session State Initialization
###################################

defaults = {
    "results_ready": False,
    "output_dir": None,
    "formatted_name": None,
    "commander_name": None,
    "recent": None,
    "min_price": None,
    "max_price": None,
    "deck_hashes": None,
    "all_decks": None,
    "card_counts": None,
    "type_groups": None,
    "final_status": None,
}

for key, value in defaults.items():
    st.session_state.setdefault(key, value)

###################################
# Inputs
###################################

st.header("Commander Selection")

commander_name = st.text_input(
    "Commander Name",
    # value=default_commander,
    placeholder="e.g. Atraxa, Praetors' Voice"
)

st.header("Deck Query Filters")
recent = st.number_input("How many recent decks to fetch?", 5, 200, 20, 5)
min_price = st.number_input("Minimum deck price", 5, 10000, 5, 5)
max_price = st.number_input("Maximum deck price", 5, 10000, 100, 5)

run_button = st.button("Fetch & Analyze Decklists")

if not st.session_state.results_ready and not run_button:
    st.info("Ready when you are — enter your commander and press the button!")

final_status_box = st.empty()


###################################
# Run
###################################

if run_button:
    st.session_state.results_ready = False

    if not commander_name.strip():
        st.warning("Enter a commander name first.")
        st.stop()

    active_step = st.empty()   # ⭐ SINGLE ACTIVE STEP SLOT

    formatted_name = analyzer.format_commander_name(commander_name)

    st.session_state.update(
        commander_name=commander_name,
        formatted_name=formatted_name,
        recent=int(recent),
        min_price=float(min_price),
        max_price=float(max_price),
    )

    try:
        # Step 1 — Build ID
        active_step.info("🔄 Detecting EDHREC build ID…")
        build_id = analyzer.fetch_edhrec_build_id()

        # Step 2 — Deck Table
        active_step.info("🔄 Fetching deck table…")
        deck_table = analyzer.fetch_deck_table(formatted_name)

        deck_hashes = analyzer.filter_deck_hashes(
            deck_table,
            int(recent),
            float(min_price),
            float(max_price),
        )
        st.session_state.deck_hashes = deck_hashes

        if not deck_hashes:
            active_step.warning("No decks found for those filters.")
            st.stop()

        # Step 3 — Download Decklists
        active_step.info("🔄 Downloading decklists…")
        progress = st.progress(0)
        status = st.empty()

        all_decks = []
        for completed, total, deck in analyzer.fetch_decks_with_progress(deck_hashes):
            if deck:
                all_decks.append(deck)
            progress.progress(completed / total)
            status.info(f"Downloaded {completed}/{total} decks")

        progress.empty()
        status.empty()

        st.session_state.all_decks = all_decks

        # Step 4 — Write Output Files
        active_step.info("🔄 Writing output files…")

        output_dir = analyzer.clean_output_directories(formatted_name)
        st.session_state.output_dir = output_dir

        metadata_header = analyzer.build_metadata_header(
            commander_name,
            int(recent),
            float(min_price),
            float(max_price),
            source_info={"streamlit-ui": True},
        )

        analyzer.save_decklists(all_decks, output_dir, formatted_name, metadata_header)

        # Step 5 — Count Cards
        active_step.info("🔄 Counting cards…")
        card_counts = analyzer.count_cards(all_decks)
        st.session_state.card_counts = card_counts

        analyzer.save_master_cardcount(card_counts, output_dir, metadata_header)

        # Step 6 — Classify Cards
        active_step.info("🔄 Classifying cards by type…")
        type_progress = st.progress(0)
        type_status = st.empty()

        type_groups = {
            "Creature": {},
            "Instant": {},
            "Sorcery": {},
            "Artifact": {},
            "Enchantment": {},
            "Planeswalker": {},
            "Battle": {},
            "Land": {},
            "Unknown": {},
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

            type_progress.progress(idx / total_cards)
            type_status.info(f"Downloaded {idx}/{total_cards} decks")
        
        type_progress.empty()
        type_status.empty()
        active_step.empty()

        st.session_state.type_groups = type_groups
        analyzer.save_cardtypes(type_groups, output_dir, metadata_header)

        # Done
        st.session_state.final_status = "success"
        st.session_state.results_ready = True


    except Exception as e:
        st.session_state.final_status = "error"
        final_status_box.error(f"❌ Error: {e}")
        st.stop()

    
if st.session_state.final_status == "success":
    final_status_box.success("✅ Processing complete!")
elif st.session_state.final_status == "error":
    final_status_box.error("❌ Processing failed.")


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
    file_data = {
        fn: open(os.path.join(output_dir, fn), "rb").read()
        for fn in output_files
    }

    zip_buffer = io.BytesIO()
    with zipfile.ZipFile(zip_buffer, "w", zipfile.ZIP_DEFLATED) as zipf:
        for fn, data in file_data.items():
            zipf.writestr(fn, data)

    st.download_button(
        "📦 Download All as ZIP",
        zip_buffer.getvalue(),
        f"{formatted_name}_edhrec_output.zip",
        "application/zip",
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
    st.subheader("Card Analysis Dashboard")

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
