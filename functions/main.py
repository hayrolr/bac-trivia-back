import random # Para seleccionar pregunta aleatoria
import datetime
import firebase_admin
from firebase_admin import firestore
from google.cloud.firestore_v1.client import Client as FirestoreClient
from google.cloud.firestore_v1.transaction import Transaction, transactional
from flask import Flask, request, jsonify # Flask solo para jsonify si no se usa como router
from firebase_functions import https_fn
import re # Para limpiar nombre


# --- Configuración de CORS ---
# Para desarrollo, '*' temporal.
# Para producción, se deben especificar los dominios:
# ALLOWED_ORIGINS = ["http://localhost:3000", "https://app-en-vercel.vercel.app"]
ALLOWED_ORIGINS = "*" # Temporalmente para desarrollo y pruebas


# Inicialization
try:
    firebase_admin.initialize_app()
    db: FirestoreClient = firestore.client()
    print("Firebase Admin SDK initialized successfully.")
except Exception as e:
    print(f"Error initializing Firebase Admin SDK: {e}")
    db = None


# --- Transactional Helper for Counter ---
@transactional
def get_next_user_number(transaction: Transaction, counter_ref) -> int:
    """Obtiene e incrementa el contador de usuarios de forma atómica."""
    snapshot = counter_ref.get(transaction=transaction)
    current_number = snapshot.get('current_number')
    if current_number is None: current_number = 0 # Inicializa si no existe
    next_number = current_number + 1
    transaction.update(counter_ref, {'current_number': next_number})
    return next_number


# --- Funciones para construir respuestas CORS ---
# FUNCIONES IMPORTANTES
def _build_cors_preflight_response():
    # En Flask, se puedes crear una respuesta vacía y añadir cabeceras.
    # Para https_fn.Response directamente:
    headers = {
        'Access-Control-Allow-Origin': ALLOWED_ORIGINS,
        'Access-Control-Allow-Methods': 'GET, POST, PUT, DELETE, OPTIONS', # Métodos permitidos
        'Access-Control-Allow-Headers': 'Content-Type, Authorization', # Cabeceras permitidas
        'Access-Control-Max-Age': '3600' # Cuánto tiempo el navegador puede cachear esta respuesta preflight
    }
    return https_fn.Response(status=204, headers=headers) # 204 No Content


def _add_cors_headers(response_data, status_code=200):
    # Esta función ahora toma los datos y el código de estado,
    # y construye un https_fn.Response con jsonify y cabeceras CORS.
    # Nota: jsonify es de Flask. Si no se usa Flask, debemos usar json.dumps()
    # y establecer Content-Type: application/json manualmente.

    # Si response_data no es ya un string JSON (como lo sería con jsonify().data)
    # lo convertimos. Asumiendo que response_data es un dict.
    json_response_body = jsonify(response_data).data # .data retorna el string JSON de la respuesta Flask

    headers = {
        'Access-Control-Allow-Origin': ALLOWED_ORIGINS,
        'Content-Type': 'application/json'
    }
    return https_fn.Response(response=json_response_body, status=status_code, headers=headers)


# --- Cloud Function: registerUser ---
@https_fn.on_request()
def registerUser(req: https_fn.Request) -> https_fn.Response:
    if req.method == 'OPTIONS':
        return _build_cors_preflight_response()

    """Registra un nuevo usuario generando un UsuarioID único."""
    if db is None:
        response_body = {"error": "Server Error", "message": "Firebase not initialized"}
        return _add_cors_headers(response_body, 500)

    try:
        req_data = req.get_json(silent=True)
        if not req_data:
            response_body = {"error": "Bad Request", "message": "Missing JSON body"}
            return _add_cors_headers(response_body, 400)

        cedula = req_data.get('cedula')
        nombre = req_data.get('nombre')
        apellido = req_data.get('apellido')

        if not all([cedula, nombre, apellido]):
            response_body = {"error": "Bad Request", "message": "Missing required fields (cedula, nombre, apellido)"}
            return _add_cors_headers(response_body, 400)

        # Limpiar nombre para usar en UsuarioID (solo letras y números del primer nombre)
        nombre_limpio = re.sub(r'\W+', '', nombre.lower().strip().split(' ')[0])
        if not nombre_limpio: nombre_limpio = "usuario" # Fallback

        if not (3 <= len(nombre.strip()) <= 80):
            response_body = {"error": "Bad Request", "message": "Nombre debe tener entre 3 y 80 caracteres."}
            return _add_cors_headers(response_body, 400)
        if not (3 <= len(apellido.strip()) <= 80):
            response_body = {"error": "Bad Request", "message": "Apellido debe tener entre 3 y 80 caracteres."}
            return _add_cors_headers(response_body, 400)
        if len(cedula.strip()) != 16:  # Validacion basica de cédula
            response_body = {"error": "Bad Request", "message": "Cédula inválida."}
            return _add_cors_headers(response_body, 400)

        users_ref = db.collection('users')

        # 1. Verificar si la Cédula ya existe (Query) - ¡Importante para evitar duplicados!
        cedula_query = users_ref.where('cedula', '==', cedula).limit(1).stream()
        if next(cedula_query, None):
            response_body = {"error": "Conflict", "message": f"User with Cedula {cedula} already exists"}
            return _add_cors_headers(response_body, 409)

        # 2. Obtener el siguiente número (transaccional)
        counter_doc_ref = db.collection('counters').document('user_counter')
        try:
            next_number = get_next_user_number(db.transaction(), counter_doc_ref)
        except Exception as counter_error:
             print(f"ERROR getting next user number: {counter_error}")
             response_body = {"error": "Internal Server Error", "message": "Could not generate user number"}
             return _add_cors_headers(response_body, 500)

        # 3. Generar UsuarioID
        usuario_id_generado = f"{nombre_limpio}-{next_number}"
        # Posible verificación extra: ¿Ya existe este usuarioId generado? (Muy improbable pero posible)
        # usuario_id_query = users_ref.where('usuarioId', '==', usuario_id_generado).limit(1).stream()
        # if next(usuario_id_query, None):
        #     # Lógica de reintento o error si hay colisión (extremadamente raro con contador)
        #     print(f"WARNING: usuarioId collision detected for {usuario_id_generado}")
        #     return https_fn.Response("Internal Server Error: usuarioId collision", status=500)


        # 4. Crear el nuevo documento de usuario (con ID automático de Firestore)
        new_user_data = {
            'nombre': nombre,
            'apellido': apellido,
            'cedula': cedula,
            'usuarioId': usuario_id_generado, # El ID generado para login
            'puntos': 0,
            'itemsCollected': [],
            'lastPlayedTotem': {}
        }
        update_time, new_doc_ref = users_ref.add(new_user_data) # add() genera ID automático

        response_body = {
            "firestoreId": new_doc_ref.id,
            "usuarioId": usuario_id_generado, # Devuelve el ID para que el usuario lo use en login
            "message": "User registered successfully"
        }
        # Usa jsonify de Flask para convertir dict a JSON correctamente
        return _add_cors_headers(response_body, 201)

    except Exception as e:
        print(f"ERROR in registerUser: {e}")
        # import traceback; print(traceback.format_exc()) # Para debug detallado
        return _add_cors_headers({"error": "Internal Server Error"}, 500)


# --- Cloud Function: loginUser ---
@https_fn.on_request()
def loginUser(req: https_fn.Request) -> https_fn.Response:
    if req.method == 'OPTIONS':
        return _build_cors_preflight_response()

    """Autentica un usuario buscando su **usuarioId**."""
    if db is None:
        response_body = {"error": "Server Error", "message": "Firebase not initialized"}
        return _add_cors_headers(response_body, 500)

    try:
        req_data = req.get_json(silent=True)
        if not req_data:
            response_body = {"error": "Bad Request", "message": "Missing JSON body"}
            return _add_cors_headers(response_body, 400)

        # ** El usuario envía 'usuarioId' para login **
        usuario_id_login = req_data.get('usuarioId')
        if not usuario_id_login:
            response_body = {"error": "Bad Request", "message": "Missing usuarioId field for login"}
            return _add_cors_headers(response_body, 400)

        users_ref = db.collection('users')
        # Buscar el usuario por el campo 'usuarioId'
        query = users_ref.where('usuarioId', '==', usuario_id_login).limit(1)
        results = query.stream()
        user_doc = next(results, None) # Obtiene el primer documento que coincida o None

        if not user_doc:
            response_body = {"error": "Not Found", "message": f"User with UsuarioID '{usuario_id_login}' not found"}
            return _add_cors_headers(response_body, 404)

        user_data = user_doc.to_dict()
        # Devolver los datos necesarios para el estado del frontend
        response_body = {
            "firestoreId": user_doc.id, # El ID interno de Firestore
            "usuarioId": user_data.get("usuarioId"), # El ID que usó para login
            "nombre": user_data.get("nombre"),
            "apellido": user_data.get("apellido"),
            "puntos": user_data.get("puntos", 0),
            "itemsCollected": user_data.get("itemsCollected", []),
            "lastPlayedTotem": user_data.get("lastPlayedTotem", {})
        }
        # Usa jsonify de Flask para convertir dict a JSON correctamente
        return _add_cors_headers(response_body, 200)

    except Exception as e:
        print(f"ERROR in loginUser: {e}")
        # import traceback; print(traceback.format_exc())
        return _add_cors_headers({"error": "Internal Server Error"}, 500)


# --- Cloud Function: getTriviaQuestion ---
@https_fn.on_request()
def getTriviaQuestion(req: https_fn.Request) -> https_fn.Response:
    if req.method == 'OPTIONS':
        return _build_cors_preflight_response()

    if db is None:
        response_body = {"error": "Server Error", "message": "Firebase not initialized"}
        return _add_cors_headers(response_body, 500)

    try:
        req_data = req.get_json(silent=True)
        if not req_data:
            response_body = {"error": "Bad Request", "message": "Missing JSON body"}
            return _add_cors_headers(response_body, 400)

        user_firestore_id = req_data.get('userFirestoreId') # Usaremos el ID de documento de Firestore del usuario
        qr_code_data = req_data.get('qrCodeData') # El dato leído del QR del tótem

        if not all([user_firestore_id, qr_code_data]):
            response_body = {"error": "Bad Request", "message": "Missing userFirestoreId or qrCodeData"}
            return _add_cors_headers(response_body, 400)

        # 1. Obtener datos del usuario
        user_doc_ref = db.collection('users').document(user_firestore_id)
        user_doc = user_doc_ref.get()
        if not user_doc.exists:
            response_body = {"error": "Not Found", "message": "User not found"}
            return _add_cors_headers(response_body, 404)

        user_data = user_doc.to_dict()

        # 2. Obtener datos del tótem y su categoría
        totems_ref = db.collection('totems')
        totem_query = totems_ref.where('qrCodeData', '==', qr_code_data).limit(1).stream()
        totem_doc = next(totem_query, None)
        if not totem_doc:
            response_body = {"error": "Not Found", "message": "Totem with specified QR data not found"}
            return _add_cors_headers(response_body, 404)

        totem_data = totem_doc.to_dict()
        totem_id = totem_doc.id # ID del documento del tótem
        category = totem_data.get('category')
        if not category:
            response_body = {"error": "Server Error", "message": "Totem has no category assigned"}
            return _add_cors_headers(response_body, 500)

        # 3. Verificar lógica "Espera 5 minutos"
        last_played_totem_info = user_data.get('lastPlayedTotem', {}).get(totem_id)
        if last_played_totem_info:
            last_attempt_time_str = last_played_totem_info.get('timestamp')
            attempt_was_correct = last_played_totem_info.get('attemptCorrect', True) # TODO: Por el momento, asumir correcto si no está

            if last_attempt_time_str and not attempt_was_correct:
                # Convertir timestamp string (ISO format) a datetime object
                last_attempt_time = datetime.datetime.fromisoformat(last_attempt_time_str)
                current_time = datetime.datetime.now(datetime.timezone.utc) # Usar UTC
                time_difference = current_time - last_attempt_time

                if time_difference.total_seconds() < 300: # 300 segundos = 5 minutos
                    response_body = {
                        "status": "wait",
                        "message": "Debes esperar 5 minutos para volver a intentar en este tótem.",
                        "cooldown_seconds_left": int(300 - time_difference.total_seconds())
                    }
                    return _add_cors_headers(response_body, 429) # Too Many Requests (o un código custom)

        # 4. Obtener preguntas de la categoría
        trivias_ref = db.collection('trivias')
        category_questions_query = trivias_ref.where('category', '==', category).stream()
        all_category_questions = {doc.id: doc.to_dict() for doc in category_questions_query}

        if not all_category_questions:
            response_body = {"status": "no_questions_found", "message": f"No hay trivias para la categoría '{category}'."}
            return _add_cors_headers(response_body, 404)

        # 5. Filtrar preguntas ya respondidas correctamente por el usuario
        answered_correctly_ids = set()
        for item in user_data.get('itemsCollected', []):
            if item.get('answeredCorrectly') and item.get('totemId') == totem_id: # O solo item.get('triviaId')
                answered_correctly_ids.add(item.get('triviaId'))

        available_questions = {
            qid: qdata for qid, qdata in all_category_questions.items() if qid not in answered_correctly_ids
        }

        if not available_questions:
            response_body = {"status": "category_completed", "message": f"¡Felicidades! Has completado todas las trivias de la categoría '{category}' en este tótem."}
            return _add_cors_headers(response_body, 200)

        # 6. Seleccionar una pregunta aleatoria de las disponibles
        selected_question_id = random.choice(list(available_questions.keys()))
        selected_question_data = available_questions[selected_question_id]

        # 7. Preparar y devolver la pregunta (sin la respuesta correcta)
        response_question = {
            "triviaId": selected_question_id,
            "category": selected_question_data.get('category'),
            "questionText": selected_question_data.get('questionText'),
            "options": selected_question_data.get('options'),
            "totemId": totem_id # Enviar el ID del tótem para referencia
        }

        return _add_cors_headers(response_question, 200)

    except Exception as e:
        print(f"ERROR in getTriviaQuestion: {e}")
        # import traceback; print(traceback.format_exc())
        return _add_cors_headers({"error": "Internal Server Error"}, 500)
