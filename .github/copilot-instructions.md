# Copilot Instructions for HRCOB-FCA-INPI-v5

## Project Overview
This is a Python/Streamlit application for intelligence-grade due-diligence screening for UK charities and companies. It includes advanced features like Bayesian Risk Scoring, Fuzzy Entity Matching, Adverse Media Intelligence, and Financial Pattern Analysis.

## Setup Checklist

- [x] Verify that the copilot-instructions.md file in the .github directory is created.

- [x] Clarify Project Requirements
  - Project Type: Python (Streamlit application)
  - Main Framework: Streamlit 1.54.0
  - Key Dependencies: LangGraph, Google GenAI, OpenAI, Plotly
  - Primary Language: Python 3.x

- [ ] Scaffold the Project
  - Repository already cloned
  - Need to configure Python environment
  - Install dependencies from requirements.txt

- [ ] Customize the Project
  - Project already has full codebase
  - May need to review core modules and configuration
  - Skip detailed customization unless user requests changes

- [ ] Install Required Extensions
  - Python extension for VS Code (if not already installed)
  - No additional extensions required for Streamlit development

- [ ] Compile the Project
  - Install Python dependencies
  - Run diagnostics to check for import errors

- [ ] Create and Run Task
  - Create Streamlit run task for development
  - Can run: `streamlit run app.py`

- [ ] Launch the Project
  - Start Streamlit development server
  - Application available at http://localhost:8501

- [ ] Ensure Documentation is Complete
  - Review README.md
  - Ensure copilot-instructions.md is complete and clean

## Key Project Files
- `app.py` - Main Streamlit application (352KB)
- `config.py` - Configuration settings
- `core/` - Core modules for risk scoring, entity matching, etc.
- `api_clients/` - API integration clients
- `pipeline/` - Data pipeline modules
- `ui/` - UI components
- `requirements.txt` - Python dependencies

## Getting Started
1. Configure Python environment with `configure_python_environment`
2. Install dependencies: `pip install -r requirements.txt`
3. Set up any required API keys in environment variables
4. Run: `streamlit run app.py`
