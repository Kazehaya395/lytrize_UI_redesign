"""modules/analysis -- Pure analysis/chart runners.

This package used to also hold a Streamlit config-panel/registry layer
(``ANALYSIS_OPTIONS``, ``_WIDGET_SPEC``, the ``_run()`` dispatcher).
That layer has been fully replaced by the headless
``services/analysis_service.py``, which defines its own
``ANALYSIS_OPTIONS`` / ``WIDGET_SPEC`` / ``run_analysis`` and imports
the individual ``run_*`` functions from the submodules in this package
directly. Nothing here should import Streamlit.
"""
