# upload_trivias.py
import json
import firebase_admin
from firebase_admin import credentials, firestore

# IMPORTANTE: Descarga tu archivo de clave de servicio desde
# Consola Firebase -> Proyecto -> Configuración del Proyecto -> Cuentas de Servicio
# -> Generar nueva clave privada (JSON). ¡Guárdalo de forma segura!
SERVICE_ACCOUNT_KEY_PATH = './bactrivia-firebase-adminsdk-fbsvc-d9f5f5d771.json'
TRIVIAS_JSON_PATH = './trivias_data.json' # Ruta a tu archivo JSON de trivias

def upload_trivias_to_firestore():
    try:
        cred = credentials.Certificate(SERVICE_ACCOUNT_KEY_PATH)
        firebase_admin.initialize_app(cred)
        db = firestore.client()
        print("Firebase Admin SDK initialized for script.")
    except Exception as e:
        print(f"Error initializing Firebase Admin SDK for script: {e}")
        return

    try:
        with open(TRIVIAS_JSON_PATH, 'r', encoding='utf-8') as f:
            trivias_data = json.load(f)
    except FileNotFoundError:
        print(f"Error: El archivo {TRIVIAS_JSON_PATH} no fue encontrado.")
        return
    except json.JSONDecodeError:
        print(f"Error: El archivo {TRIVIAS_JSON_PATH} no es un JSON válido.")
        return

    trivias_collection_ref = db.collection('trivias')
    batch = db.batch()
    count = 0

    # Opcional: Borrar trivias existentes para evitar duplicados si se ejecuta varias veces
    # existing_trivias = trivias_collection_ref.stream()
    # for doc in existing_trivias:
    #     print(f"Deleting existing trivia: {doc.id}")
    #     batch.delete(doc.reference)
    # batch.commit() # Commit de borrado
    # batch = db.batch() # Reiniciar batch para adiciones
    # print("Existing trivias deleted.")


    print(f"Cargando {len(trivias_data)} trivias a Firestore...")
    for trivia in trivias_data:
        # Firestore generará un ID automático si no se especifica
        doc_ref = trivias_collection_ref.document()
        batch.set(doc_ref, trivia)
        count += 1
        if count % 400 == 0: # Firestore batch limit es 500 operaciones
            print(f"Committing batch of {count}...")
            batch.commit()
            batch = db.batch() # Nuevo batch

    if count % 400 != 0: # Commit del último batch si no estaba vacío
        batch.commit()

    print(f"¡Carga completada! {count} trivias añadidas/actualizadas.")

if __name__ == '__main__':
    upload_trivias_to_firestore()
