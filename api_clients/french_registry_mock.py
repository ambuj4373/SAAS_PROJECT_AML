# MOCK FRENCH COMPANY DATA FOR TESTING
# This provides test data when the real INPI API is not available

MOCK_FRENCH_COMPANIES = {
    "732043259": {
        "name": "Michelin & Cie",
        "legal_form": "Société anonyme",
        "status": "Active",
        "creation_date": "1889-05-28",
        "address": "Place de la Concorde",
        "postal_code": "75008",
        "city": "Paris",
        "website": "https://www.michelin.com",
    },
    "498061394": {
        "name": "Orange",
        "legal_form": "Société anonyme",
        "status": "Active",
        "creation_date": "1988-01-01",
        "address": "78 rue Olivier de Serres",
        "postal_code": "75015",
        "city": "Paris",
        "website": "https://www.orange.fr",
    },
    "775670691": {
        "name": "L'Oréal SA",
        "legal_form": "Société anonyme",
        "status": "Active",
        "creation_date": "1909-07-30",
        "address": "14 rue Royale",
        "postal_code": "75008",
        "city": "Paris",
        "website": "https://www.loreal.com",
    },
    "595083853": {
        "name": "BNP Paribas",
        "legal_form": "Société anonyme",
        "status": "Active",
        "creation_date": "2000-05-23",
        "address": "16 Boulevard des Italiens",
        "postal_code": "75009",
        "city": "Paris",
        "website": "https://www.bnpparibas.com",
    },
    "672193231": {
        "name": "Carrefour SA",
        "legal_form": "Société anonyme",
        "status": "Active",
        "creation_date": "1959-06-04",
        "address": "33 avenue Émile Zola",
        "postal_code": "92100",
        "city": "Boulogne-Billancourt",
        "website": "https://www.carrefour.fr",
    },
}

MOCK_SEARCH_DATA = {
    "michelin": ["732043259"],
    "orange": ["498061394"],
    "l'oreal": ["775670691"],
    "bnp": ["595083853"],
    "carrefour": ["672193231"],
    "wise": [],  # Known to not exist in French registry
}
