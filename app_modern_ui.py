"""
Modern 3D Professional UI for PDF AI Summarizer Pro
Ultra-modern glassmorphism design with 3D effects and animations
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

# Ultra-Modern 3D Professional CSS with Glassmorphism
MODERN_CSS = """
<style>
    :root {
        --primary-gradient: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
        --secondary-gradient: linear-gradient(135deg, #f093fb 0%, #f5576c 100%);
        --tertiary-gradient: linear-gradient(135deg, #4facfe 0%, #00f2fe 100%);
        --dark-gradient: linear-gradient(135deg, #1a1a2e 0%, #16213e 100%);
        --glass-bg: rgba(255, 255, 255, 0.7);
        --glass-border: rgba(255, 255, 255, 0.2);
    }

    * {
        box-sizing: border-box;
    }

    /* Main Container */
    .main {
        background: var(--dark-gradient);
        position: relative;
        overflow-x: hidden;
    }

    .main::before {
        content: '';
        position: fixed;
        top: -50%;
        right: -50%;
        width: 200%;
        height: 200%;
        background: 
            radial-gradient(circle at 20% 50%, rgba(102, 126, 234, 0.1) 0%, transparent 50%),
            radial-gradient(circle at 80% 80%, rgba(245, 87, 108, 0.1) 0%, transparent 50%);
        animation: gradient-shift 15s ease infinite;
        pointer-events: none;
        z-index: 0;
    }

    @keyframes gradient-shift {
        0%, 100% { transform: translate(0, 0) rotate(0deg); }
        50% { transform: translate(100px, 100px) rotate(180deg); }
    }

    /* Glass Morphism Effect */
    .glass-card {
        background: var(--glass-bg);
        backdrop-filter: blur(10px);
        border: 1px solid var(--glass-border);
        border-radius: 20px;
        padding: 2rem;
        box-shadow: 
            0 8px 32px 0 rgba(31, 38, 135, 0.37),
            0 0 0 1px rgba(255, 255, 255, 0.1);
        transition: all 0.3s cubic-bezier(0.23, 1, 0.320, 1);
        position: relative;
        z-index: 1;
    }

    .glass-card:hover {
        transform: translateY(-5px) scale(1.02);
        box-shadow: 
            0 15px 35px 0 rgba(31, 38, 135, 0.5),
            0 0 0 1px rgba(255, 255, 255, 0.15);
        background: rgba(255, 255, 255, 0.85);
    }

    /* 3D Card Effect */
    .card-3d {
        perspective: 1000px;
        position: relative;
    }

    .card-3d-content {
        background: linear-gradient(135deg, rgba(255, 255, 255, 0.8) 0%, rgba(255, 255, 255, 0.6) 100%);
        border-radius: 20px;
        padding: 1.5rem;
        border: 1px solid rgba(255, 255, 255, 0.2);
        box-shadow: 
            0 10px 40px rgba(0, 0, 0, 0.15),
            inset 0 1px 0 rgba(255, 255, 255, 0.6);
        transform-style: preserve-3d;
        transition: transform 0.6s cubic-bezier(0.23, 1, 0.320, 1);
        position: relative;
    }

    .card-3d-content:hover {
        transform: rotateX(5deg) rotateY(5deg) translateZ(20px);
        box-shadow: 
            0 20px 60px rgba(0, 0, 0, 0.3),
            inset 0 1px 0 rgba(255, 255, 255, 0.8);
    }

    /* Modern Header */
    .modern-header {
        background: var(--primary-gradient);
        background-clip: text;
        -webkit-background-clip: text;
        -webkit-text-fill-color: transparent;
        font-size: 3.5rem;
        font-weight: 900;
        margin-bottom: 0.5rem;
        letter-spacing: -2px;
        text-shadow: 0 2px 10px rgba(0, 0, 0, 0.1);
        animation: fade-in-down 0.8s ease-out;
    }

    .modern-subheader {
        color: rgba(255, 255, 255, 0.7);
        font-size: 1.2rem;
        font-weight: 300;
        letter-spacing: 1px;
        animation: fade-in-up 0.8s ease-out 0.2s both;
    }

    @keyframes fade-in-down {
        from { opacity: 0; transform: translateY(-20px); }
        to { opacity: 1; transform: translateY(0); }
    }

    @keyframes fade-in-up {
        from { opacity: 0; transform: translateY(20px); }
        to { opacity: 1; transform: translateY(0); }
    }

    /* Modern Buttons */
    .modern-btn {
        background: var(--primary-gradient);
        border: none;
        border-radius: 12px;
        padding: 12px 32px;
        font-weight: 600;
        font-size: 1rem;
        color: white;
        cursor: pointer;
        transition: all 0.3s cubic-bezier(0.23, 1, 0.320, 1);
        box-shadow: 0 10px 30px rgba(102, 126, 234, 0.3);
        text-transform: uppercase;
        letter-spacing: 1.5px;
        position: relative;
        overflow: hidden;
    }

    .modern-btn::before {
        content: '';
        position: absolute;
        top: 50%;
        left: 50%;
        width: 0;
        height: 0;
        border-radius: 50%;
        background: rgba(255, 255, 255, 0.3);
        transform: translate(-50%, -50%);
        transition: width 0.6s, height 0.6s;
    }

    .modern-btn:hover::before {
        width: 300px;
        height: 300px;
    }

    .modern-btn:hover {
        transform: translateY(-3px);
        box-shadow: 0 20px 40px rgba(102, 126, 234, 0.4);
    }

    .modern-btn:active {
        transform: translateY(-1px);
    }

    /* Gradient Text */
    .gradient-text {
        background: linear-gradient(135deg, #667eea, #764ba2, #f093fb);
        background-size: 200% 200%;
        -webkit-background-clip: text;
        -webkit-text-fill-color: transparent;
        background-clip: text;
        animation: gradient-animation 3s ease infinite;
    }

    @keyframes gradient-animation {
        0% { background-position: 0% 50%; }
        50% { background-position: 100% 50%; }
        100% { background-position: 0% 50%; }
    }

    /* Modern Metrics Grid */
    .metrics-grid {
        display: grid;
        grid-template-columns: repeat(auto-fit, minmax(250px, 1fr));
        gap: 1.5rem;
        margin: 2rem 0;
    }

    .metric-card {
        background: var(--glass-bg);
        backdrop-filter: blur(10px);
        border: 1px solid var(--glass-border);
        border-radius: 16px;
        padding: 1.5rem;
        text-align: center;
        transition: all 0.3s ease;
        position: relative;
        overflow: hidden;
    }

    .metric-card::before {
        content: '';
        position: absolute;
        top: -50%;
        right: -50%;
        width: 200%;
        height: 200%;
        background: var(--primary-gradient);
        opacity: 0;
        transition: opacity 0.3s ease;
        z-index: -1;
    }

    .metric-card:hover {
        transform: translateY(-10px) scale(1.05);
        box-shadow: 0 15px 40px rgba(102, 126, 234, 0.3);
    }

    .metric-value {
        font-size: 2.5rem;
        font-weight: 900;
        background: var(--primary-gradient);
        -webkit-background-clip: text;
        -webkit-text-fill-color: transparent;
        background-clip: text;
        margin: 0.5rem 0;
    }

    .metric-label {
        font-size: 0.9rem;
        color: rgba(255, 255, 255, 0.6);
        text-transform: uppercase;
        letter-spacing: 1px;
        font-weight: 600;
    }

    /* Modern Input Field */
    .streamlit-expanderHeader {
        background: var(--glass-bg) !important;
        backdrop-filter: blur(10px) !important;
        border-radius: 12px !important;
        border: 1px solid var(--glass-border) !important;
        transition: all 0.3s ease !important;
    }

    .streamlit-expanderHeader:hover {
        background: rgba(255, 255, 255, 0.85) !important;
        box-shadow: 0 8px 24px rgba(102, 126, 234, 0.2) !important;
    }

    /* Modern Section */
    .modern-section {
        background: var(--glass-bg);
        backdrop-filter: blur(10px);
        border: 1px solid var(--glass-border);
        border-radius: 20px;
        padding: 2rem;
        margin: 1.5rem 0;
        box-shadow: 0 8px 32px 0 rgba(31, 38, 135, 0.37);
    }

    .section-title {
        font-size: 1.8rem;
        font-weight: 700;
        background: var(--primary-gradient);
        -webkit-background-clip: text;
        -webkit-text-fill-color: transparent;
        background-clip: text;
        margin-bottom: 1.5rem;
        display: flex;
        align-items: center;
        gap: 1rem;
    }

    /* Progress Bar */
    .progress-bar-container {
        background: rgba(255, 255, 255, 0.1);
        border-radius: 20px;
        height: 8px;
        overflow: hidden;
        box-shadow: inset 0 2px 4px rgba(0, 0, 0, 0.1);
    }

    .progress-bar-fill {
        background: var(--secondary-gradient);
        height: 100%;
        border-radius: 20px;
        box-shadow: 0 0 20px rgba(245, 87, 108, 0.4);
        animation: progress-pulse 1s ease-in-out infinite;
    }

    @keyframes progress-pulse {
        0%, 100% { box-shadow: 0 0 20px rgba(245, 87, 108, 0.4); }
        50% { box-shadow: 0 0 30px rgba(245, 87, 108, 0.6); }
    }

    /* Status Badge */
    .status-badge {
        display: inline-block;
        padding: 0.5rem 1rem;
        border-radius: 50px;
        font-weight: 600;
        font-size: 0.85rem;
        text-transform: uppercase;
        letter-spacing: 1px;
        box-shadow: 0 4px 15px rgba(0, 0, 0, 0.2);
    }

    .status-success {
        background: linear-gradient(135deg, #11998e, #38ef7d);
        color: white;
    }

    .status-warning {
        background: linear-gradient(135deg, #f093fb, #f5576c);
        color: white;
    }

    .status-info {
        background: linear-gradient(135deg, #4facfe, #00f2fe);
        color: white;
    }

    /* Sidebar Enhancement */
    [data-testid="stSidebar"] {
        background: var(--dark-gradient);
    }

    /* Text Styling */
    h1, h2, h3 {
        color: rgba(255, 255, 255, 0.95);
        font-weight: 700;
        letter-spacing: -0.5px;
    }

    p {
        color: rgba(255, 255, 255, 0.7);
        line-height: 1.6;
        font-weight: 300;
    }

    /* File Upload Area */
    .upload-area {
        background: linear-gradient(135deg, rgba(102, 126, 234, 0.1), rgba(245, 87, 108, 0.1));
        border: 2px dashed rgba(255, 255, 255, 0.3);
        border-radius: 16px;
        padding: 3rem;
        text-align: center;
        transition: all 0.3s ease;
        cursor: pointer;
    }

    .upload-area:hover {
        border-color: rgba(255, 255, 255, 0.5);
        background: linear-gradient(135deg, rgba(102, 126, 234, 0.2), rgba(245, 87, 108, 0.2));
        box-shadow: 0 10px 30px rgba(102, 126, 234, 0.2);
    }

    /* Success/Error Messages */
    .success-box {
        background: linear-gradient(135deg, rgba(17, 153, 142, 0.2), rgba(56, 239, 125, 0.2));
        border-left: 4px solid #38ef7d;
        border-radius: 12px;
        padding: 1.5rem;
        color: #11998e;
        font-weight: 500;
    }

    .error-box {
        background: linear-gradient(135deg, rgba(245, 87, 108, 0.2), rgba(240, 147, 251, 0.2));
        border-left: 4px solid #f5576c;
        border-radius: 12px;
        padding: 1.5rem;
        color: #f5576c;
        font-weight: 500;
    }

    .info-box {
        background: linear-gradient(135deg, rgba(79, 172, 254, 0.2), rgba(0, 242, 254, 0.2));
        border-left: 4px solid #4facfe;
        border-radius: 12px;
        padding: 1.5rem;
        color: #4facfe;
        font-weight: 500;
    }

    /* Animation Loader */
    @keyframes spin {
        0% { transform: rotate(0deg); }
        100% { transform: rotate(360deg); }
    }

    .spinner {
        border: 4px solid rgba(255, 255, 255, 0.1);
        border-top: 4px solid #667eea;
        border-radius: 50%;
        width: 40px;
        height: 40px;
        animation: spin 1s linear infinite;
    }

    /* Smooth Scroll */
    html {
        scroll-behavior: smooth;
    }

    /* Custom Scrollbar */
    ::-webkit-scrollbar {
        width: 10px;
    }

    ::-webkit-scrollbar-track {
        background: rgba(255, 255, 255, 0.05);
    }

    ::-webkit-scrollbar-thumb {
        background: linear-gradient(180deg, #667eea, #764ba2);
        border-radius: 5px;
    }

    ::-webkit-scrollbar-thumb:hover {
        background: linear-gradient(180deg, #764ba2, #f093fb);
    }
</style>
"""

# Apply Modern CSS
st.markdown(MODERN_CSS, unsafe_allow_html=True)


def render_hero_section():
    """Render modern hero section"""
    col1, col2 = st.columns([1, 1])
    
    with col1:
        st.markdown("""
            <div style="padding: 3rem 0;">
                <h1 class="modern-header">PDF AI<br>Summarizer<br>Pro</h1>
                <p class="modern-subheader">Next-Generation Document Intelligence</p>
            </div>
        """, unsafe_allow_html=True)
        
        st.markdown("""
            <div style="margin-top: 2rem; display: flex; gap: 1rem;">
                <div class="status-badge status-success">⚡ Ultra-Fast</div>
                <div class="status-badge status-info">🔒 Secure</div>
                <div class="status-badge status-success">✅ Reliable</div>
            </div>
        """, unsafe_allow_html=True)
    
    with col2:
        st.markdown("""
            <div class="card-3d" style="margin-top: 1rem;">
                <div class="card-3d-content">
                    <div style="font-size: 4rem; text-align: center;">📄✨</div>
                    <p style="text-align: center; color: #667eea; font-weight: 700; font-size: 1.2rem;">
                        Powered by Advanced AI
                    </p>
                </div>
            </div>
        """, unsafe_allow_html=True)


def render_modern_metric(label, value, emoji=""):
    """Render a modern metric card"""
    return f"""
        <div class="metric-card">
            <div style="font-size: 2rem;">{emoji}</div>
            <div class="metric-value">{value}</div>
            <div class="metric-label">{label}</div>
        </div>
    """


def main():
    # Render hero section
    render_hero_section()
    
    st.divider()

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

    # Main layout with modern sidebar
    with st.sidebar:
        st.markdown("""
            <div style="text-align: center; padding: 1.5rem 0; margin-bottom: 2rem;">
                <h2 style="color: rgba(255, 255, 255, 0.95); font-size: 1.8rem; margin: 0;">⚙️ Configuration</h2>
                <p style="color: rgba(255, 255, 255, 0.5); margin-top: 0.5rem;">Customize your experience</p>
            </div>
        """, unsafe_allow_html=True)

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
                help="Quality vs speed trade-off"
            )

        # Advanced features
        with st.expander("✨ Advanced Features", expanded=True):
            show_analysis = st.checkbox("Document Analysis", value=True)
            show_quotes = st.checkbox("Extract Key Quotes", value=False)
            show_keywords = st.checkbox("Extract Keywords", value=True)
            show_sentiment = st.checkbox("Sentiment Analysis", value=True)
            show_readability = st.checkbox("Readability Score", value=True)
            show_metrics = st.checkbox("Processing Metrics", value=True)

        # Cache and session
        with st.expander("💾 Storage & Cache"):
            enable_cache = st.checkbox(
                "Enable Caching",
                value=Config.ENABLE_CACHING,
                help="Cache results to avoid reprocessing"
            )
            col1, col2 = st.columns(2)
            with col1:
                if st.button("🗑️ Clear Cache", use_container_width=True):
                    cache_manager.clear_all()
                    st.success("Cache cleared!")
            with col2:
                if st.button("📊 Session Stats", use_container_width=True):
                    stats = session_manager.get_session_stats()
                    st.json(stats)

        # Batch processing
        with st.expander("📦 Batch Processing"):
            batch_processing = st.checkbox(
                "Enable Batch Mode",
                value=False,
                help="Process multiple PDFs at once"
            )

    # Main content
    st.markdown("""
        <div class="modern-section">
            <div class="section-title">📤 Upload & Process</div>
    """, unsafe_allow_html=True)

    col1, col2 = st.columns([2, 1])

    with col1:
        if batch_processing:
            uploaded_files = st.file_uploader(
                "Choose PDF files",
                type="pdf",
                accept_multiple_files=True,
                help="Upload one or more PDF documents"
            )
        else:
            uploaded_file = st.file_uploader(
                "Choose a PDF file",
                type="pdf",
                help="Upload a PDF document to summarize"
            )
            uploaded_files = [uploaded_file] if uploaded_file else []

    with col2:
        if uploaded_files:
            metrics_html = render_modern_metric("Files Selected", len([f for f in uploaded_files if f]), "📁")
            st.markdown(f"<div class='metrics-grid'>{metrics_html}</div>", unsafe_allow_html=True)

    st.markdown("</div>", unsafe_allow_html=True)

    # Process files
    if uploaded_files and any(uploaded_files):
        if len(uploaded_files) == 1 and not batch_processing:
            process_single_file_modern(
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
            process_batch_files_modern(
                [f for f in uploaded_files if f],
                summary_type,
                max_tokens,
                show_analysis,
                show_quotes,
                show_keywords,
                enable_cache
            )


def process_single_file_modern(
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
    """Process single file with modern UI"""
    
    # Validate file
    is_valid, validation_message = file_validator.validate_file(uploaded_file)
    if not is_valid:
        st.markdown(f"<div class='error-box'>{validation_message}</div>", unsafe_allow_html=True)
        return

    # Check rate limit
    if not rate_limiter.is_allowed():
        retry_after = rate_limiter.get_retry_after()
        st.markdown(f"<div class='warning-box'>Rate limit exceeded. Try again in {retry_after}s</div>", unsafe_allow_html=True)
        return

    st.markdown(f"<div class='success-box'>✅ File uploaded: {uploaded_file.name}</div>", unsafe_allow_html=True)

    # Display metadata
    st.markdown("<div class='modern-section'><div class='section-title'>📋 Document Analysis</div>", unsafe_allow_html=True)
    
    with st.spinner("📖 Analyzing PDF metadata..."):
        metadata = st.session_state.pdf_processor.get_pdf_metadata(uploaded_file)

    metrics_html = render_modern_metric("Pages", metadata.get('num_pages', '?'), "📄")
    metrics_html += render_modern_metric("Author", metadata.get('author', 'Unknown')[:15], "👤")
    metrics_html += render_modern_metric("Size", f"{file_validator.get_file_size_mb(uploaded_file):.2f} MB", "💾")
    
    st.markdown(f"<div class='metrics-grid'>{metrics_html}</div>", unsafe_allow_html=True)
    st.markdown("</div>", unsafe_allow_html=True)

    # Check cache
    file_content = uploaded_file.getvalue()
    from cache_manager import CacheManager
    file_hash = CacheManager._compute_file_hash(file_content)
    cached_result = cache_manager.get(uploaded_file.name, file_hash, summary_type) if enable_cache else None

    if cached_result:
        st.markdown("<div class='info-box'>💾 Using cached result (Instant!)</div>", unsafe_allow_html=True)
        display_cached_results_modern(cached_result, show_keywords, show_sentiment, show_readability, show_metrics)
        return

    # Process button
    col1, col2, col3 = st.columns([1, 1, 1])
    with col2:
        if st.button("🚀 Process PDF", use_container_width=True, key="process_btn"):
            process_pdf_logic_modern(
                uploaded_file, file_content, file_hash, summary_type, max_tokens,
                show_analysis, show_quotes, show_keywords, show_sentiment,
                show_readability, show_metrics, enable_cache
            )


def process_pdf_logic_modern(
    uploaded_file, file_content, file_hash, summary_type, max_tokens,
    show_analysis, show_quotes, show_keywords, show_sentiment,
    show_readability, show_metrics, enable_cache
):
    """Main PDF processing logic with modern UI"""
    start_time = time.time()
    
    try:
        # Extract text
        with st.spinner("📖 Extracting text..."):
            raw_text = st.session_state.pdf_processor.extract_text_from_pdf(uploaded_file)
            st.markdown(f"<div class='success-box'>✅ Extracted {len(raw_text)} characters</div>", unsafe_allow_html=True)

        # Clean text
        with st.spinner("🧹 Cleaning text..."):
            clean_text = st.session_state.pdf_processor.clean_text(raw_text)
            token_count = st.session_state.pdf_processor.count_tokens(clean_text)
            st.markdown(f"<div class='success-box'>✅ Cleaned: {len(clean_text)} chars, ~{token_count} tokens</div>", unsafe_allow_html=True)

        # Chunk text
        with st.spinner("✂️ Splitting text..."):
            chunks = st.session_state.pdf_processor.chunk_text(clean_text, max_tokens)
            st.markdown(f"<div class='success-box'>✅ Created {len(chunks)} chunks</div>", unsafe_allow_html=True)

        # Document analysis
        if show_analysis:
            with st.spinner("🔍 Analyzing structure..."):
                analysis = st.session_state.summarizer.analyze_document_structure(clean_text)
                if analysis['status'] == 'success':
                    with st.expander("📊 Structure Analysis"):
                        st.write(analysis['analysis'])

        # Generate summaries
        with st.spinner(f"🤖 Generating {summary_type} summary..."):
            summary_data = st.session_state.summarizer.summarize_chunks(chunks, summary_type)
            st.markdown("<div class='success-box'>✅ Summary generated!</div>", unsafe_allow_html=True)

        # Extract quotes
        if show_quotes:
            with st.spinner("💬 Extracting quotes..."):
                quotes = st.session_state.summarizer.extract_key_quotes(clean_text[:5000])
                with st.expander("💬 Key Quotes"):
                    for i, quote in enumerate(quotes, 1):
                        st.markdown(f"**{i}.** _{quote}_")

        # Advanced features
        if show_keywords:
            with st.spinner("🔍 Extracting keywords..."):
                keywords = keyword_extractor.extract_keywords(clean_text)
                with st.expander("🔑 Top Keywords"):
                    keyword_cols = st.columns(5)
                    for i, kw in enumerate(keywords[:5]):
                        with keyword_cols[i]:
                            st.markdown(f"<div class='status-badge status-info'>{kw}</div>", unsafe_allow_html=True)

        if show_sentiment:
            with st.spinner("💭 Analyzing sentiment..."):
                sentiment = sentiment_analyzer.analyze_sentiment(clean_text)
                with st.expander("💭 Sentiment Analysis"):
                    col1, col2, col3 = st.columns(3)
                    with col1:
                        st.markdown(render_modern_metric("Sentiment", sentiment['sentiment'].upper(), "😊" if sentiment['score'] > 0 else "😟"), unsafe_allow_html=True)
                    with col2:
                        st.markdown(render_modern_metric("Score", f"{sentiment['score']:.2f}", "📊"), unsafe_allow_html=True)
                    with col3:
                        st.markdown(render_modern_metric("Positive", sentiment['positive_words'], "✅"), unsafe_allow_html=True)

        if show_readability:
            with st.spinner("📖 Calculating readability..."):
                readability = readability_analyzer.calculate_readability_score(clean_text)
                with st.expander("📖 Readability Analysis"):
                    col1, col2, col3 = st.columns(3)
                    with col1:
                        st.markdown(render_modern_metric("Level", readability['reading_level'], "📚"), unsafe_allow_html=True)
                    with col2:
                        st.markdown(render_modern_metric("Complexity", f"{readability['complexity_score']:.1f}", "🎯"), unsafe_allow_html=True)
                    with col3:
                        st.markdown(render_modern_metric("Words/Sent", f"{readability['avg_words_per_sentence']:.1f}", "✍️"), unsafe_allow_html=True)

        # Display results
        st.markdown("<div class='modern-section'><div class='section-title'>📋 Summary Results</div>", unsafe_allow_html=True)
        format_summary_display(summary_data)
        st.markdown("</div>", unsafe_allow_html=True)

        # Processing metrics
        if show_metrics:
            processing_time = time.time() - start_time
            combined_summary = " ".join([s.get('summary', '') for s in summary_data])
            metrics = processing_metrics.calculate_metrics(clean_text, combined_summary, processing_time, len(chunks))
            
            with st.expander("📊 Processing Metrics"):
                metrics_html = f"""
                    {render_modern_metric("Compression", f"{metrics['compression_ratio']:.2f}x", "📦")}
                    {render_modern_metric("Time", f"{metrics['processing_time_seconds']:.2f}s", "⏱️")}
                    {render_modern_metric("Words", f"{metrics['summary_words']}", "📝")}
                    {render_modern_metric("Speed", f"{metrics['avg_time_per_chunk']:.2f}s/chunk", "⚡")}
                """
                st.markdown(f"<div class='metrics-grid'>{metrics_html}</div>", unsafe_allow_html=True)

        # Export
        st.markdown("<div class='modern-section'><div class='section-title'>📤 Export Results</div>", unsafe_allow_html=True)
        col1, col2 = st.columns(2)
        with col1:
            summary_text = export_summary_to_text(summary_data, uploaded_file.name)
            st.download_button(
                label="📄 Download Text",
                data=summary_text,
                file_name=f"{uploaded_file.name}_summary.txt",
                mime="text/plain",
                use_container_width=True
            )
        with col2:
            summary_df = create_summary_dataframe(summary_data)
            csv_data = summary_df.to_csv(index=False)
            st.download_button(
                label="📊 Download CSV",
                data=csv_data,
                file_name=f"{uploaded_file.name}_stats.csv",
                mime="text/csv",
                use_container_width=True
            )
        st.markdown("</div>", unsafe_allow_html=True)

        # Cache and track
        if enable_cache:
            cache_manager.set(uploaded_file.name, file_hash, summary_type, summary_data)
            st.markdown("<div class='info-box'>💾 Result cached for future use</div>", unsafe_allow_html=True)

        session_manager.add_session(
            file_name=uploaded_file.name,
            file_size=len(file_content),
            summary_type=summary_type,
            processing_time=time.time() - start_time,
            num_chunks=len(chunks),
            status="success"
        )

    except Exception as e:
        st.markdown(f"<div class='error-box'>{error_handler.handle_processing_error(e, 'processing')}</div>", unsafe_allow_html=True)
        logger.error(f"Processing error: {str(e)}")


def process_batch_files_modern(uploaded_files, summary_type, max_tokens, show_analysis, show_quotes, show_keywords, enable_cache):
    """Batch processing with modern UI"""
    st.markdown(f"<div class='info-box'>📦 Processing {len(uploaded_files)} file(s)</div>", unsafe_allow_html=True)

    if st.button("🚀 Process All", use_container_width=True):
        progress_bar = st.progress(0)
        results = []

        for idx, file in enumerate(uploaded_files):
            st.info(f"⏳ Processing {idx + 1}/{len(uploaded_files)}: {file.name}")
            try:
                file_content = file.getvalue()
                from cache_manager import CacheManager
                file_hash = CacheManager._compute_file_hash(file_content)
                cached = cache_manager.get(file.name, file_hash, summary_type) if enable_cache else None
                
                if cached:
                    st.success(f"✅ {file.name} (cached)")
                    results.append({"file": file.name, "status": "success", "source": "cache"})
                else:
                    raw_text = st.session_state.pdf_processor.extract_text_from_pdf(file)
                    clean_text = st.session_state.pdf_processor.clean_text(raw_text)
                    chunks = st.session_state.pdf_processor.chunk_text(clean_text, max_tokens)
                    summary_data = st.session_state.summarizer.summarize_chunks(chunks, summary_type)
                    
                    if enable_cache:
                        cache_manager.set(file.name, file_hash, summary_type, summary_data)
                    
                    st.success(f"✅ {file.name}")
                    results.append({"file": file.name, "status": "success", "source": "processed"})
            
            except Exception as e:
                st.error(f"❌ {file.name}: {str(e)}")
                results.append({"file": file.name, "status": "failed", "error": str(e)})
            
            progress_bar.progress((idx + 1) / len(uploaded_files))

        st.markdown(f"<div class='success-box'>✅ Batch processing complete!</div>", unsafe_allow_html=True)


def display_cached_results_modern(cached_result, show_keywords, show_sentiment, show_readability, show_metrics):
    """Display cached results with modern UI"""
    st.markdown("<div class='modern-section'><div class='section-title'>📋 Cached Results</div>", unsafe_allow_html=True)
    format_summary_display(cached_result)
    st.markdown("</div>", unsafe_allow_html=True)


if __name__ == "__main__":
    main()
