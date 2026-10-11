"""Point d'entrée unique des deux Function Apps (même code source, deux rôles).

Le paramètre d'application FUNCTION_APP_ROLE, posé par Terraform, choisit les fonctions
enregistrées (mécanisme des blueprints Azure Functions) :

    main   (défaut) : route_incoming + pseudonymise_job   -> functions_main.py
    images          : prepare_image                       -> functions_images.py (OpenCV)

L'app principale n'importe jamais le code images : OpenCV n'y est ni chargé ni installé.
"""

import os

import azure.functions as func

ROLE_MAIN = "main"
ROLE_IMAGES = "images"

app = func.FunctionApp()

role = os.environ.get("FUNCTION_APP_ROLE", ROLE_MAIN).strip().lower()
if role == ROLE_IMAGES:
    from functions_images import bp
elif role == ROLE_MAIN:
    from functions_main import bp
else:
    raise RuntimeError(f"FUNCTION_APP_ROLE inconnu : {role!r} (attendu : {ROLE_MAIN} ou {ROLE_IMAGES})")

app.register_functions(bp)
