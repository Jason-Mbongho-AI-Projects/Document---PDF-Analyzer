"""
Enhanced PDF AI Summarizer with advanced features
Supports caching, session history, batch processing, and more
"""
import streamlit as st
import os
import time
from pdf_processor import PDFProcessor
from summarizer import PDFSummarizer
from utils import (
    validate_api_key,
    estimate_processing_time,
    display_processing_status,
    format_summary_display,
    export_summary_to_text,
    create_summary_dataframe,
)
from cache_manager import cache_manager
from session_manager import session_manager
from validators import file_validator, rate_limiter, timeout_manager
from advanced_features import (
    keyword_extractor, sentiment_analyzer, readability_analyzer,
    processing_metrics, document_insights
)
from error_handler import error_handler, retry_with_backoff
from logger_config import setup_logger
from config import Config

logger = setup_logger(__name__)

# Streamlit page configuration
st.set_page_config(
    page_title="PDF AI Summarizer Pro",
    page_icon="📄",
    layout="wide",
    initial_sidebar_state="expanded"
)

# Custom CSS for enhanced UI
st.markdown("""
<style>
    .main-header {
        font-size: 2.5rem;
        font-weight: bold;
        color: #0078D4;
        margin-bottom: 1rem;
    }
    .metrics-container {
        display: grid;
        grid-template-columns: repeat(auto-fit, minmax(150px, 1fr));
        gap: 1rem;
        margin: 1rem 0;
    }
    .highlight {
        background-color: #FFF4E6;
        padding: 1rem;
        border-radius: 0.5rem;
        border-left: 4px solid #FF9800;
    }
</style>
""", unsafe_allow_html=True)


def main():
    # Title and description
    st.markdown('<div class="main-header">📄 PDF AI Summarizer Pro</div>', unsafe_allow_html=True)
    st.markdown("Advanced PDF analysis powered by OpenRouter - with caching, batch processing, and analytics")

    # Validate API key
    if not validate_api_key():
        st.stop()

    # Initialize session state
    if 'pdf_processor' not in st.session_state:
        st.session_state.pdf_processor = PDFProcessor()
    if 'summarizer' not in st.session_state:
        st.session_state.summarizer = PDFSummarizer()
    if 'processed_files' not in st.session_state:
        st.session_state.processed_files = []

    # Sidebar configuration
    with st.sidebar:
        st.header("⚙️ Settings & Configuration")

        # Processing settings
        with st.expander("📋 Processing Settings", expanded=True):
            summary_type = st.selectbox(
                "Summary Type",
                Config.SUPPORTED_SUMMARY_TYPES,
                index=Config.SUPPORTED_SUMMARY_TYPES.index(Config.DEFAULT_SUMMARY_TYPE),
                help="Choose the type of summary you want"
            )

            max_tokens = st.slider(
                "Max Tokens per Chunk",
                Config.MIN_TOKENS_PER_CHUNK,
                10000,
                Config.MAX_TOKENS_PER_CHUNK,
                help="Maximum tokens per text chunk (affects processing speed)"
            )

        # Feature toggles
        with st.expander("✨ Advanced Features", expanded=True):
            show_analysis = st.checkbox("Document Analysis", value=True)
            show_quotes = st.checkbox("Extract Key Quotes", value=False)
            show_keywords = st.checkbox("Extract Keywords", value=True, help="Extract top keywords from document")
            show_sentiment = st.checkbox("Sentiment Analysis", value=True)
            show_readability = st.checkbox("Readability Score", value=True)
            show_metrics = st.checkbox("Processing Metrics", value=True)

        # Caching and session settings
        with st.expander("💾 Storage & Cache"):
            enable_cache = st.checkbox(
                "Enable Caching",
                value=Config.ENABLE_CACHING,
                help="Cache results to avoid reprocessing"
            )
            if st.button("🗑️ Clear Cache"):
                cache_manager.clear_all()
                st.success("Cache cleared!")

            if st.button("📊 View Session Stats"):
                stats = session_manager.get_session_stats()
                st.json(stats)

        # Batch processing
        with st.expander("📦 Batch Processing"):
            batch_processing = st.checkbox(
                "Enable Batch Mode",
                value=False,
                help="Process multiple PDFs at once"
            )

    # Main content area
    col1, col2 = st.columns([2, 1])

    with col1:
        st.subheader("📤 Upload PDF(s)")
        if batch_processing:
            uploaded_files = st.file_uploader(
                "Choose PDF files",
                type="pdf",
                accept_multiple_files=True,
                help="Upload one or more PDF documents to process"
            )
        else:
            uploaded_file = st.file_uploader(
                "Choose a PDF file",
                type="pdf",
                help="Upload a PDF document to summarize"
            )
            uploaded_files = [uploaded_file] if uploaded_file else []

    with col2:
        st.subheader("📊 Quick Stats")
        if uploaded_files:
            st.metric("Files Selected", len([f for f in uploaded_files if f]))

    # Process files
    if uploaded_files and any(uploaded_files):
        st.divider()

        if len(uploaded_files) == 1 and not batch_processing:
            # Single file processing
            process_single_file(
                uploaded_files[0],
                summary_type,
                max_tokens,
                show_analysis,
                show_quotes,
                show_keywords,
                show_sentiment,
                show_readability,
                show_metrics,
                enable_cache
            )
        else:
            # Batch processing
            process_batch_files(
                [f for f in uploaded_files if f],
                summary_type,
                max_tokens,
                show_analysis,
                show_quotes,
                show_keywords,
                enable_cache
            )


def process_single_file(
    uploaded_file,
    summary_type,
    max_tokens,
    show_analysis,
    show_quotes,
    show_keywords,
    show_sentiment,
    show_readability,
    show_metrics,
    enable_cache
):
    """Process a single PDF file"""

    # Validate file
    is_valid, validation_message = file_validator.validate_file(uploaded_file)
    if not is_valid:
        st.error(validation_message)
        return

    # Check rate limit
    if not rate_limiter.is_allowed():
        retry_after = rate_limiter.get_retry_after()
        st.error(f"Rate limit exceeded. Please try again in {retry_after} seconds.")
        logger.warning(f"Rate limit exceeded by user")
        return

    st.success(f"✅ File uploaded: {uploaded_file.name} ({file_validator.get_file_size_mb(uploaded_file):.2f} MB)")

    # Display metadata
    with st.spinner("📖 Analyzing PDF metadata..."):
        metadata = st.session_state.pdf_processor.get_pdf_metadata(uploaded_file)

    col1, col2, col3, col4 = st.columns(4)
    with col1:
        st.metric("📄 Pages", metadata.get('num_pages', 'Unknown'))
    with col2:
        st.metric("👤 Author", metadata.get('author', 'Unknown')[:20])
    with col3:
        st.metric("📋 Title", metadata.get('title', 'Unknown')[:20])
    with col4:
        file_size_mb = file_validator.get_file_size_mb(uploaded_file)
        st.metric("💾 Size", f"{file_size_mb:.2f} MB")

    # Check cache
    file_content = uploaded_file.getvalue()
    from cache_manager import CacheManager
    file_hash = CacheManager._compute_file_hash(file_content)
    cached_result = cache_manager.get(uploaded_file.name, file_hash, summary_type) if enable_cache else None

    if cached_result:
        st.info("💾 Using cached result from previous processing")
        display_cached_results(cached_result, show_keywords, show_sentiment, show_readability, show_metrics)
        return

    # Process button
    if st.button("🚀 Process PDF", type="primary"):
        start_time = time.time()
        try:
            # Extract text
            with st.spinner("📖 Extracting text from PDF..."):
                raw_text = st.session_state.pdf_processor.extract_text_from_pdf(uploaded_file)
                st.success(f"✅ Extracted {len(raw_text)} characters")

            # Clean text
            with st.spinner("🧹 Cleaning and processing text..."):
                clean_text = st.session_state.pdf_processor.clean_text(raw_text)
                token_count = st.session_state.pdf_processor.count_tokens(clean_text)
                st.success(f"✅ Cleaned: {len(clean_text)} chars, ~{token_count} tokens")

            # Chunk text
            with st.spinner("✂️ Splitting text into chunks..."):
                chunks = st.session_state.pdf_processor.chunk_text(clean_text, max_tokens)
                st.success(f"✅ Created {len(chunks)} chunks")
                estimated_time = estimate_processing_time(len(chunks))
                st.info(f"⏱️ Estimated time: {estimated_time}")

            # Document analysis
            if show_analysis:
                with st.spinner("🔍 Analyzing document structure..."):
                    analysis = st.session_state.summarizer.analyze_document_structure(clean_text)
                    if analysis['status'] == 'success':
                        with st.expander("📊 Document Analysis"):
                            st.write(analysis['analysis'])

            # Generate summaries
            with st.spinner(f"🤖 Generating {summary_type} summaries..."):
                progress_bar = st.progress(0)
                try:
                    summary_data = st.session_state.summarizer.summarize_chunks(chunks, summary_type)
                    progress_bar.progress(1.0)
                    st.success("✅ Summaries generated successfully!")
                except Exception as e:
                    st.error(error_handler.handle_api_error(e, "summary generation"))
                    logger.error(f"Error during summarization: {str(e)}")
                    return

            # Extract quotes
            if show_quotes:
                with st.spinner("💬 Extracting key quotes..."):
                    quotes = st.session_state.summarizer.extract_key_quotes(clean_text[:5000])
                    with st.expander("💬 Key Quotes"):
                        for i, quote in enumerate(quotes, 1):
                            st.write(f"{i}. *\"{quote}\"*")

            # Advanced features
            if show_keywords:
                with st.spinner("🔍 Extracting keywords..."):
                    keywords = keyword_extractor.extract_keywords(clean_text)
                    with st.expander("🔑 Top Keywords"):
                        st.write(", ".join(keywords[:15]))

            if show_sentiment:
                with st.spinner("💭 Analyzing sentiment..."):
                    sentiment = sentiment_analyzer.analyze_sentiment(clean_text)
                    with st.expander("💭 Sentiment Analysis"):
                        col1, col2, col3 = st.columns(3)
                        col1.metric("Sentiment", sentiment['sentiment'].upper())
                        col2.metric("Score", sentiment['score'])
                        col3.metric("Positive Words", sentiment['positive_words'])

            if show_readability:
                with st.spinner("📖 Calculating readability..."):
                    readability = readability_analyzer.calculate_readability_score(clean_text)
                    with st.expander("📖 Readability Analysis"):
                        col1, col2, col3 = st.columns(3)
                        col1.metric("Reading Level", readability['reading_level'])
                        col2.metric("Avg Words/Sentence", readability['avg_words_per_sentence'])
                        col3.metric("Complexity", readability['complexity_score'])

            # Display results
            st.header("📋 Summary Results")
            format_summary_display(summary_data)

            # Processing metrics
            if show_metrics:
                processing_time = time.time() - start_time
                combined_summary = " ".join([s.get('summary', '') for s in summary_data])
                metrics = processing_metrics.calculate_metrics(clean_text, combined_summary, processing_time, len(chunks))
                
                with st.expander("📊 Processing Metrics"):
                    col1, col2, col3, col4 = st.columns(4)
                    col1.metric("Compression Ratio", f"{metrics['compression_ratio']:.2f}x")
                    col2.metric("Processing Time", f"{metrics['processing_time_seconds']:.2f}s")
                    col3.metric("Original Words", metrics['original_words'])
                    col4.metric("Summary Words", metrics['summary_words'])

            # Export options
            st.subheader("📤 Export Options")
            col1, col2 = st.columns(2)

            with col1:
                summary_text = export_summary_to_text(summary_data, uploaded_file.name)
                st.download_button(
                    label="📄 Download as Text",
                    data=summary_text,
                    file_name=f"{uploaded_file.name}_summary.txt",
                    mime="text/plain"
                )

            with col2:
                summary_df = create_summary_dataframe(summary_data)
                csv_data = summary_df.to_csv(index=False)
                st.download_button(
                    label="📊 Download as CSV",
                    data=csv_data,
                    file_name=f"{uploaded_file.name}_stats.csv",
                    mime="text/csv"
                )

            # Cache result
            if enable_cache:
                cache_manager.set(uploaded_file.name, file_hash, summary_type, summary_data)
                st.info("💾 Result cached for future use")

            # Add to session history
            processing_time = time.time() - start_time
            session_manager.add_session(
                file_name=uploaded_file.name,
                file_size=len(file_content),
                summary_type=summary_type,
                processing_time=processing_time,
                num_chunks=len(chunks),
                status="success"
            )

        except Exception as e:
            st.error(error_handler.handle_processing_error(e, "file processing"))
            logger.error(f"Processing error: {str(e)}")
            session_manager.add_session(
                file_name=uploaded_file.name,
                file_size=len(file_content),
                summary_type=summary_type,
                processing_time=time.time() - start_time,
                num_chunks=0,
                status="failed"
            )


def process_batch_files(uploaded_files, summary_type, max_tokens, show_analysis, show_quotes, show_keywords, enable_cache):
    """Process multiple PDF files in batch"""
    st.info(f"📦 Batch Processing Mode: {len(uploaded_files)} file(s)")

    if st.button("🚀 Process All Files", type="primary"):
        progress_bar = st.progress(0)
        results = []

        for idx, uploaded_file in enumerate(uploaded_files):
            st.info(f"Processing {idx + 1}/{len(uploaded_files)}: {uploaded_file.name}")

            try:
                file_content = uploaded_file.getvalue()
                from cache_manager import CacheManager
                file_hash = CacheManager._compute_file_hash(file_content)

                # Check cache
                cached_result = cache_manager.get(uploaded_file.name, file_hash, summary_type) if enable_cache else None
                if cached_result:
                    st.success(f"✅ {uploaded_file.name} - (from cache)")
                    results.append({"file": uploaded_file.name, "status": "success", "source": "cache"})
                else:
                    # Process file
                    raw_text = st.session_state.pdf_processor.extract_text_from_pdf(uploaded_file)
                    clean_text = st.session_state.pdf_processor.clean_text(raw_text)
                    chunks = st.session_state.pdf_processor.chunk_text(clean_text, max_tokens)
                    summary_data = st.session_state.summarizer.summarize_chunks(chunks, summary_type)

                    if enable_cache:
                        cache_manager.set(uploaded_file.name, file_hash, summary_type, summary_data)

                    st.success(f"✅ {uploaded_file.name} - Processed")
                    results.append({"file": uploaded_file.name, "status": "success", "source": "processed"})

            except Exception as e:
                st.error(f"❌ {uploaded_file.name} - {str(e)}")
                results.append({"file": uploaded_file.name, "status": "failed", "error": str(e)})

            progress_bar.progress((idx + 1) / len(uploaded_files))

        # Summary
        st.success(f"✅ Batch processing complete!")
        success_count = sum(1 for r in results if r["status"] == "success")
        st.metric("Success Rate", f"{success_count}/{len(uploaded_files)}")


def display_cached_results(cached_result, show_keywords, show_sentiment, show_readability, show_metrics):
    """Display previously cached results"""
    st.header("📋 Cached Summary Results")
    format_summary_display(cached_result)


if __name__ == "__main__":
    main()
