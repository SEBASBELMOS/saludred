"""Clinical RAG engine: chunking, lexical and dense retrieval, fusion and answering.

The pure-Python core (``text``, ``lexical``, ``fusion``, ``chunking``) has no
third-party dependency: the API image carries neither numpy nor torch. Dense
vectors come from the ``ml`` service (``app.services.ml_client``).
"""
