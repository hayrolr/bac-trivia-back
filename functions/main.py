# main.py
import random
import datetime

import bcrypt
import firebase_admin
from firebase_admin import firestore, credentials
from google.cloud.firestore_v1.client import Client as FirestoreClient
from google.cloud.firestore_v1.transaction import Transaction, transactional
from google.cloud.firestore_v1.base_query import FieldFilter  # Importar para queries más complejas si es necesario

from flask import Flask, request, jsonify  # Flask solo para jsonify si no se usa como router
from firebase_functions import https_fn  # , options as fn_options # Para configurar CORS a nivel de función

from functools import wraps
import re

# --- Constants ---
POINTS_PER_TRIVIA_CORRECT = 3
POINTS_PER_GOLDEN_TRIVIA_CORRECT = 6

# --- Configuración de CORS ---
# fn_options.set_global_options(cors=fn_options.CorsOptions(cors_origins="*", cors_methods=["GET", "POST", "PUT", "DELETE", "OPTIONS"]))
# Alternativamente, manejar manualmente como ya está implementado:
ALLOWED_ORIGINS = "*"  # Temporalmente para desarrollo y pruebas

# Inicialización
try:
    # cred = credentials.ApplicationDefault() # Opcional, para emulador local podría necesitarse service account
    if not firebase_admin._apps:  # Evitar reinicializar
        firebase_admin.initialize_app()
    db: FirestoreClient = firestore.client()
    print("Firebase Admin SDK initialized successfully.")
except Exception as e:
    print(f"Error initializing Firebase Admin SDK: {e}")
    db = None


# --- Transactional Helper for Counter (sin cambios) ---
@transactional
def get_next_user_number(transaction: Transaction, counter_ref) -> int:
    snapshot = counter_ref.get(transaction=transaction)
    current_number = snapshot.get('current_number')
    if current_number is None: current_number = 0
    next_number = current_number + 1
    transaction.update(counter_ref, {'current_number': next_number})
    return next_number


# --- Helper para obtener el estado de la aplicación ---
def get_app_status():
    if db is None:
        # Si la DB no está inicializada, asumimos que la app no está disponible
        # o manejamos como un error crítico del sistema.
        print("ERROR: DB not initialized in get_app_status. Defaulting to app inactive.")
        return False
    try:
        status_doc_ref = db.collection('app_config').document('status')
        status_doc = status_doc_ref.get()
        if status_doc.exists:
            return status_doc.to_dict().get('isAppActive', True) # Default a True si el campo falta
        return True # Default a True si el documento de config no existe (app activa)
    except Exception as e:
        print(f"ERROR reading app status: {e}. Defaulting to app active.")
        return True # En caso de error leyendo, default a True para no bloquear innecesariamente

# --- Decorador para verificar el estado de la aplicación ---
def check_app_active(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if not get_app_status():
            # Mensaje es opcional, el frontend manejará el mensaje principal
            return _add_cors_headers({"error": "Service Unavailable", "message": "Application is currently disabled."}, 503)
        return f(*args, **kwargs)
    return decorated_function


# --- Funciones para construir respuestas CORS (sin cambios) ---
def _build_cors_preflight_response():
    headers = {
        'Access-Control-Allow-Origin': ALLOWED_ORIGINS,
        'Access-Control-Allow-Methods': 'GET, POST, PUT, DELETE, OPTIONS',
        'Access-Control-Allow-Headers': 'Content-Type, Authorization',
        'Access-Control-Max-Age': '3600'
    }
    return https_fn.Response(status=204, headers=headers)


def _add_cors_headers(response_data, status_code=200):
    # Usando Flask para jsonify. Si no se usa Flask en el entorno de Cloud Functions,
    # se debería usar json.dumps y establecer Content-Type manualmente.
    # Para Firebase Functions, es común devolver un dict y la plataforma lo maneja,
    # o construir https_fn.Response con json.dumps(response_data).
    # Por simplicidad y consistencia con el código original, mantendremos jsonify
    # asumiendo que el entorno de ejecución de Firebase lo maneja o que Flask está disponible.
    # Si no, esto necesitaría un ajuste a:
    # import json
    # body = json.dumps(response_data)
    # headers = {'Access-Control-Allow-Origin': ALLOWED_ORIGINS, 'Content-Type': 'application/json'}
    # return https_fn.Response(response=body, status=status_code, headers=headers)

    # Código original que usa Flask jsonify:
    json_response_body = jsonify(response_data).data
    headers = {
        'Access-Control-Allow-Origin': ALLOWED_ORIGINS,
        'Content-Type': 'application/json'
    }
    return https_fn.Response(response=json_response_body, status=status_code, headers=headers)


# --- Cloud Function: registerUser (sin cambios significativos, ya guarda itemsCollected y lastPlayedTotem vacíos) ---
@check_app_active
@https_fn.on_request()
def registerUser(req: https_fn.Request) -> https_fn.Response:
    if req.method == 'OPTIONS':
        return _build_cors_preflight_response()
    if db is None:
        return _add_cors_headers({"error": "Server Error", "message": "Firebase not initialized"}, 500)
    try:
        req_data = req.get_json(silent=True)
        if not req_data:
            return _add_cors_headers({"error": "Bad Request", "message": "Missing JSON body"}, 400)

        nombre = req_data.get('nombre')
        apellido = req_data.get('apellido', '')
        cedula = req_data.get('cedula', '')

        if not nombre:
            return _add_cors_headers({"error": "Bad Request", "message": "Missing required field: nombre"}, 400)

        # Use the provided username directly, case-insensitive check
        usuario_id = re.sub(r'\W+', '', nombre.lower().strip().split(' ')[0]) or "usuario"
        users_ref = db.collection('users')
        # Case-insensitive check for existing username
        existing_users_query = users_ref.where(filter=FieldFilter('usuarioId', '==', usuario_id)).limit(1).stream()

        if next(existing_users_query, None):
            return _add_cors_headers({"error": "Conflict", "message": "Este Usuario ya existe... por favor, usa uno diferente"}, 409)

        new_user_data = {
            'nombre': nombre,
            'apellido': apellido,
            'cedula': cedula,
            'usuarioId': usuario_id,
            'puntos': 0,
            'itemsCollected': [],
            'lastPlayedTotem': {}
        }
        _update_time, new_doc_ref = users_ref.add(new_user_data)
        return _add_cors_headers({
            "firestoreId": new_doc_ref.id,
            "usuarioId": usuario_id,
            "message": "User registered successfully"
        }, 201)
    except Exception as e:
        print(f"ERROR in registerUser: {e}")
        return _add_cors_headers({"error": "Internal Server Error"}, 500)


# --- Cloud Function: loginUser (sin cambios) ---
@check_app_active
@https_fn.on_request()
def loginUser(req: https_fn.Request) -> https_fn.Response:
    if req.method == 'OPTIONS': return _build_cors_preflight_response()
    if db is None: return _add_cors_headers({"error": "Server Error", "message": "Firebase not initialized"}, 500)
    try:
        req_data = req.get_json(silent=True)
        if not req_data: return _add_cors_headers({"error": "Bad Request", "message": "Missing JSON body"}, 400)
        usuario_id_login = req_data.get('usuarioId')
        if not usuario_id_login: return _add_cors_headers({"error": "Bad Request", "message": "Missing usuarioId"}, 400)

        users_ref = db.collection('users')
        query = users_ref.where(filter=FieldFilter('usuarioId', '==', usuario_id_login)).limit(1)
        results = query.stream()
        user_doc = next(results, None)

        if not user_doc:
            return _add_cors_headers({"error": "Not Found", "message": f"User '{usuario_id_login}' not found"}, 404)
        user_data = user_doc.to_dict()
        return _add_cors_headers({
            "firestoreId": user_doc.id, "usuarioId": user_data.get("usuarioId"),
            "nombre": user_data.get("nombre"), "apellido": user_data.get("apellido"),
            "puntos": user_data.get("puntos", 0),
            "itemsCollected": user_data.get("itemsCollected", []),  # Asegurar que siempre se devuelva
            "lastPlayedTotem": user_data.get("lastPlayedTotem", {})  # Asegurar que siempre se devuelva
        }, 200)
    except Exception as e:
        print(f"ERROR in loginUser: {e}")
        return _add_cors_headers({"error": "Internal Server Error"}, 500)


# --- Cloud Function: getTriviaQuestion (REFINADA) ---
@check_app_active
@https_fn.on_request()
def getTriviaQuestion(req: https_fn.Request) -> https_fn.Response:
    if req.method == 'OPTIONS':
        return _build_cors_preflight_response()
    if db is None:
        return _add_cors_headers({"error": "Server Error", "message": "Firebase not initialized"}, 500)

    try:
        req_data = req.get_json(silent=True)
        if not req_data:
            return _add_cors_headers({"error": "Bad Request", "message": "Missing JSON body"}, 400)

        user_firestore_id = req_data.get('userFirestoreId')
        qr_code_data = req_data.get('qrCodeData')

        if not all([user_firestore_id, qr_code_data]):
            return _add_cors_headers({"error": "Bad Request", "message": "Missing userFirestoreId or qrCodeData"}, 400)

        user_doc_ref = db.collection('users').document(user_firestore_id)
        user_doc = user_doc_ref.get()
        if not user_doc.exists:
            return _add_cors_headers({"error": "Not Found", "message": "User not found"}, 404)
        user_data = user_doc.to_dict()

        totems_ref = db.collection('totems')
        totem_query = totems_ref.where(filter=FieldFilter('qrCodeData', '==', qr_code_data)).limit(1).stream()
        totem_doc_snapshot = next(totem_query, None)
        if not totem_doc_snapshot:
            return _add_cors_headers({"error": "Not Found", "message": "Totem with specified QR data not found"}, 404)
        totem_data = totem_doc_snapshot.to_dict()
        totem_id = totem_doc_snapshot.id  # ID del documento del tótem
        category = totem_data.get('category')
        if not category:
            return _add_cors_headers({"error": "Server Error", "message": "Totem has no category assigned"}, 500)

        # Verificar lógica "Espera 5 minutos" (REFINADA)
        # Usamos totem_id como clave en lastPlayedTotem
        last_played_info_for_this_totem = user_data.get('lastPlayedTotem', {}).get(totem_id)
        if last_played_info_for_this_totem:
            last_attempt_timestamp_str = last_played_info_for_this_totem.get('timestamp')
            # El cooldown aplica si el último intento NO fue correcto.
            # Si fue correcto, el usuario ya no debería poder jugar otra pregunta de este tótem
            # hasta que se resetee o se complete la categoría (manejado por filtro de answered_correctly_ids).
            # Aquí, el cooldown es específicamente para reintentos fallidos.
            attempt_was_correct = last_played_info_for_this_totem.get('attemptCorrect',
                                                                      True)  # Default true para no bloquear si el campo falta

            if last_attempt_timestamp_str and not attempt_was_correct:  # Cooldown si el último intento fue INCORRECTO
                try:
                    # Asumiendo que el timestamp es un string ISO UTC o un objeto datetime de Firestore
                    if isinstance(last_attempt_timestamp_str, str):
                        last_attempt_time = datetime.datetime.fromisoformat(
                            last_attempt_timestamp_str.replace("Z", "+00:00"))
                    elif isinstance(last_attempt_timestamp_str, datetime.datetime):  # Firestore Timestamp
                        last_attempt_time = last_attempt_timestamp_str.replace(
                            tzinfo=datetime.timezone.utc)  # Asegurar UTC
                    else:  # Tipo desconocido
                        raise ValueError("Invalid timestamp format in lastPlayedTotem")

                    current_time = datetime.datetime.now(datetime.timezone.utc)
                    time_difference_seconds = (current_time - last_attempt_time).total_seconds()
                    cooldown_period_seconds = 3  # 3 seconds

                    if time_difference_seconds < cooldown_period_seconds:
                        return _add_cors_headers({
                            "status": "wait",
                            "message": "Debes esperar para volver a intentar en este tótem.",
                            "cooldown_seconds_left": int(cooldown_period_seconds - time_difference_seconds)
                        }, 200)  # 200 con status 'wait', o 429 si se prefiere
                except ValueError as ve:
                    print(f"Timestamp parsing error for totem {totem_id}, user {user_firestore_id}: {ve}")
                    # Continuar sin cooldown si hay error de parseo, o manejar de otra forma

        trivias_ref = db.collection('trivias')
        category_questions_query = trivias_ref.where(filter=FieldFilter('category', '==', category)).stream()
        all_category_questions = {doc.id: doc.to_dict() for doc in category_questions_query}
        if not all_category_questions:
            return _add_cors_headers(
                {"status": "no_questions_found", "message": f"No trivias for category '{category}'"}, 200)

        # Filtrar preguntas ya respondidas CORRECTAMENTE (REFINADO)
        answered_correctly_trivia_ids = set()
        # Iterar sobre itemsCollected y verificar que cada item es un diccionario
        for item in user_data.get('itemsCollected', []):
            if isinstance(item, dict) and item.get('answeredCorrectly') and item.get('triviaId'):
                # Adicionalmente, podríamos querer filtrar por totemId si una misma trivia
                # pudiera aparecer en varios tótems y queremos que se pueda responder una vez por tótem.
                # Por ahora, si una triviaId se respondió correctamente en cualquier parte, no se repite.
                # if item.get('totemId') == totem_id:
                answered_correctly_trivia_ids.add(item.get('triviaId'))
            elif not isinstance(item, dict):
                print(f"WARN: Invalid item found in itemsCollected for user {user_firestore_id}: {item}")

        available_questions = {
            qid: qdata for qid, qdata in all_category_questions.items() if qid not in answered_correctly_trivia_ids
        }

        if not available_questions:
            return _add_cors_headers({"status": "category_completed",
                                      "message": f"¡Felicidades! Has completado las trivias de '{category}'."}, 200)

        selected_question_id = random.choice(list(available_questions.keys()))
        selected_question_data = available_questions[selected_question_id]

        return _add_cors_headers({
            "triviaId": selected_question_id, "category": selected_question_data.get('category'),
            "questionText": selected_question_data.get('questionText'),
            "options": selected_question_data.get('options'),
            "totemId": totem_id
        }, 200)

    except Exception as e:
        print(f"ERROR in getTriviaQuestion: {e}")
        import traceback
        traceback.print_exc()
        return _add_cors_headers({"error": "Internal Server Error", "message": str(e)}, 500)


# --- Cloud Function: submitTriviaAnswer (NUEVA IMPLEMENTACIÓN) ---
@check_app_active
@https_fn.on_request()
def submitTriviaAnswer(req: https_fn.Request) -> https_fn.Response:
    if req.method == 'OPTIONS':
        return _build_cors_preflight_response()
    if db is None:
        return _add_cors_headers({"error": "Server Error", "message": "Firebase not initialized"}, 500)

    try:
        req_data = req.get_json(silent=True)
        if not req_data:
            return _add_cors_headers({"error": "Bad Request", "message": "Missing JSON body"}, 400)

        user_firestore_id = req_data.get('userFirestoreId')
        trivia_id = req_data.get('triviaId')  # ID de la pregunta
        selected_option_index = req_data.get('selectedOptionIndex')  # Índice de la opción seleccionada
        totem_id = req_data.get('totemId')  # ID del tótem donde se jugó (enviado por getTriviaQuestion)
        qr_code_data = req_data.get('qrCodeData')  # qrCodeData del tótem (enviado desde el frontend)
        is_golden = req_data.get('isGolden', False)  # New field

        if not all([user_firestore_id, trivia_id, totem_id, qr_code_data]) or selected_option_index is None:
            return _add_cors_headers({"error": "Bad Request", "message": "Missing required fields"}, 400)

        # Validar que selected_option_index sea un entero
        try:
            selected_option_index = int(selected_option_index)
        except ValueError:
            return _add_cors_headers({"error": "Bad Request", "message": "selectedOptionIndex must be an integer"}, 400)

        # 1. Obtener datos del usuario y de la trivia
        user_doc_ref = db.collection('users').document(user_firestore_id)
        trivia_doc_ref = db.collection('trivias').document(trivia_id)

        user_doc = user_doc_ref.get()
        trivia_doc = trivia_doc_ref.get()

        if not user_doc.exists:
            return _add_cors_headers({"error": "Not Found", "message": "User not found"}, 404)
        if not trivia_doc.exists:
            return _add_cors_headers({"error": "Not Found", "message": "Trivia question not found"}, 404)

        user_data = user_doc.to_dict()
        trivia_data = trivia_doc.to_dict()

        # 2. Verificar si esta trivia específica en este tótem ya fue intentada recientemente (opcional, pero buena idea)
        # last_played_info = user_data.get('lastPlayedTotem', {}).get(totem_id, {})
        # if last_played_info.get('triviaId') == trivia_id:
        # Podría ser un reintento muy rápido, o ya se procesó.
        # Considerar cómo manejar esto. Por ahora, permitimos el procesamiento.

        # 3. Determinar si la respuesta es correcta
        correct_answer_index = trivia_data.get('correctAnswerIndex')
        is_correct = (selected_option_index == correct_answer_index)
        points_gained = 0
        message = ""

        current_time_utc = datetime.datetime.now(datetime.timezone.utc)
        current_time_iso = current_time_utc.isoformat()

        if is_correct:
            points_gained = POINTS_PER_GOLDEN_TRIVIA_CORRECT if is_golden else POINTS_PER_TRIVIA_CORRECT
            message = "¡Respuesta Correcta!"

            # Nuevo item para itemsCollected
            collected_item_data = {
                "triviaId": trivia_id,
                "totemId": totem_id,
                "qrCodeData": qr_code_data,  # Guardar el qrCodeData también
                "category": trivia_data.get('category'),
                "answeredCorrectly": True,
                "timestamp": current_time_iso,  # O firestore.SERVER_TIMESTAMP
                "pointsGained": points_gained,
                "isGolden": is_golden
            }

            # Actualizar Firestore: sumar puntos y añadir a itemsCollected
            user_doc_ref.update({
                'puntos': firestore.Increment(points_gained),
                'itemsCollected': firestore.ArrayUnion([collected_item_data]),
                f'lastPlayedTotem.{totem_id}': {  # Actualizar el último intento para este tótem
                    'timestamp': current_time_iso,
                    'attemptCorrect': True,
                    'triviaId': trivia_id
                }
            })
        else:
            points_gained = 0  # O incluso -1 si se penaliza
            message = "Respuesta Incorrecta."
            collected_item_data = None
            # Actualizar Firestore: solo el último intento en el tótem para el cooldown
            user_doc_ref.update({
                f'lastPlayedTotem.{totem_id}': {
                    'timestamp': current_time_iso,
                    'attemptCorrect': False,
                    'triviaId': trivia_id
                }
            })

        # Obtener los puntos actualizados del usuario para devolverlos
        # (Firestore.Increment es asíncrono en el backend inmediato,
        # así que leemos de nuevo o calculamos)
        # Para simplificar, recalculamos o asumimos que el frontend puede manejarlo.
        # O mejor, leer el documento de nuevo después de la actualización si es crítico.
        # Por ahora, devolvemos los puntos ganados y el frontend suma a su estado.

        # Opcional: Leer de nuevo para obtener el total de puntos actualizado
        # updated_user_doc = user_doc_ref.get()
        # new_total_points = updated_user_doc.to_dict().get('puntos', user_data.get('puntos', 0) + points_gained)

        new_total_points = user_data.get('puntos', 0) + points_gained  # Cálculo local para la respuesta

        return _add_cors_headers({
            "correct": is_correct,
            "pointsGained": points_gained,
            "newTotalPoints": new_total_points,  # El frontend usará esto para actualizar su store
            "message": message,
            # Opcional: devolver el item recolectado para que el frontend lo añada al store
            "collectedItem": collected_item_data
        }, 200)

    except Exception as e:
        print(f"ERROR in submitTriviaAnswer: {e}")
        import traceback
        traceback.print_exc()
        return _add_cors_headers({"error": "Internal Server Error", "message": str(e)}, 500)


# --- NUEVA Cloud Function: loginAdmin ---
@https_fn.on_request()
def loginAdmin(req: https_fn.Request) -> https_fn.Response:
    if req.method == 'OPTIONS':
        return _build_cors_preflight_response()
    if db is None:
        return _add_cors_headers({"error": "Server Error", "message": "Firebase not initialized"}, 500)

    try:
        req_data = req.get_json(silent=True)
        if not req_data:
            return _add_cors_headers({"error": "Bad Request", "message": "Missing JSON body"}, 400)

        admin_id_login = req_data.get('adminId')
        password_login = req_data.get('password')

        if not all([admin_id_login, password_login]):
            return _add_cors_headers({"error": "Bad Request", "message": "Missing adminId or password"}, 400)

        admin_doc_ref = db.collection('admins').document(admin_id_login)
        admin_doc = admin_doc_ref.get()

        if not admin_doc.exists:
            return _add_cors_headers({"error": "Unauthorized", "message": "Admin ID not found"}, 401)

        admin_data = admin_doc.to_dict()
        hashed_password_stored = admin_data.get('hashedPassword')

        if not hashed_password_stored:
            print(f"ERROR: Admin {admin_id_login} has no hashedPassword stored in Firestore.")
            return _add_cors_headers({"error": "Server Error", "message": "Admin account misconfiguration"}, 500)

        # Verificar la contraseña hasheada
        if bcrypt.checkpw(password_login.encode('utf-8'), hashed_password_stored.encode('utf-8')):
            # Contraseña correcta
            return _add_cors_headers({
                "message": "Admin login successful",
                "adminId": admin_id_login,
                "nombre": admin_data.get("nombre")
                # No enviar token JWT por simplicidad en este MVP, el frontend manejará un flag
            }, 200)
        else:
            # Contraseña incorrecta
            return _add_cors_headers({"error": "Unauthorized", "message": "Invalid admin credentials"}, 401)

    except Exception as e:
        print(f"ERROR in loginAdmin: {e}")
        import traceback
        traceback.print_exc()
        return _add_cors_headers({"error": "Internal Server Error", "message": str(e)}, 500)


# --- NUEVA Cloud Function: getUsersWithScores ---
@https_fn.on_request()
def getUsersWithScores(req: https_fn.Request) -> https_fn.Response:
    if req.method == 'OPTIONS':
        return _build_cors_preflight_response()
    if db is None:
        return _add_cors_headers({"error": "Server Error", "message": "Firebase not initialized"}, 500)

    # TODO: Añadir autenticación/autorización para esta función (ej. verificar token de admin)
    # Por ahora, la dejamos abierta para la prueba del MVP.

    try:
        users_ref = db.collection('users')
        all_users_docs = users_ref.stream()  # Obtener todos los documentos

        users_list = []
        for doc in all_users_docs:
            user_data = doc.to_dict()
            users_list.append({
                "firestoreId": doc.id,  # ID del documento, útil para futuras ediciones
                "usuarioId": user_data.get("usuarioId"),
                "nombre": user_data.get("nombre"),
                "apellido": user_data.get("apellido"),
                "cedula": user_data.get("cedula"),
                "puntos": user_data.get("puntos", 0),
                # "itemsCollectedCount": len(user_data.get("itemsCollected", [])), # Opcional
            })

        # Ordenar por puntos descendente (opcional)
        users_list_sorted = sorted(users_list, key=lambda u: u['puntos'], reverse=True)

        return _add_cors_headers(users_list_sorted, 200)

    except Exception as e:
        print(f"ERROR in getUsersWithScores: {e}")
        import traceback
        traceback.print_exc()
        return _add_cors_headers({"error": "Internal Server Error", "message": str(e)}, 500)


@https_fn.on_request()
def setAppStatus(req: https_fn.Request) -> https_fn.Response:
    if req.method == 'OPTIONS':
        return _build_cors_preflight_response()
    if db is None:
        return _add_cors_headers({"error": "Server Error", "message": "Firebase not initialized"}, 500)

    # TODO: Implementar autenticación de admin, si es necesario para mayor seguridad.
    # Por ahora, estamos seguros que solo se llama desde el dashboard de admin ya protegido.

    try:
        req_data = req.get_json(silent=True)
        if not req_data or 'isActive' not in req_data or not isinstance(req_data.get('isActive'), bool):
            return _add_cors_headers({"error": "Bad Request", "message": "Missing or invalid 'isActive' boolean field"}, 400)

        is_active = req_data.get('isActive')
        status_doc_ref = db.collection('app_config').document('status')
        status_doc_ref.set({'isAppActive': is_active}, merge=True) # merge=True para crear si no existe o actualizar

        return _add_cors_headers({"message": f"Application status set to {'active' if is_active else 'inactive'}."}, 200)

    except Exception as e:
        print(f"ERROR in setAppStatus: {e}")
        import traceback
        traceback.print_exc()
        return _add_cors_headers({"error": "Internal Server Error", "message": str(e)}, 500)


@https_fn.on_request()
def getAppStatus(req: https_fn.Request) -> https_fn.Response:
    if req.method == 'OPTIONS': # Manejar preflight para GET también
        return _build_cors_preflight_response()
    if db is None:
        return _add_cors_headers({"error": "Server Error", "message": "Firebase not initialized"}, 500)

    # TODO: Autenticación de admin si se considera que solo admins deben ver esto.
    # Por ahora, es informativo.

    try:
        is_active = get_app_status() # Usar el helper ya creado
        return _add_cors_headers({"isAppActive": is_active}, 200)
    except Exception as e:
        print(f"ERROR in getAppStatus: {e}")
        return _add_cors_headers({"error": "Internal Server Error", "message": str(e)}, 500)


@https_fn.on_request()
def updateUserFromAdmin(req: https_fn.Request) -> https_fn.Response:
    if req.method == 'OPTIONS':
        return _build_cors_preflight_response()
    if db is None:
        return _add_cors_headers({"error": "Server Error", "message": "Firebase not initialized"}, 500)

    # TODO: Implementar autenticación/autorización de admin aquí si es necesario para mayor seguridad.
    # Esta función solo debe ser accesible por administradores autenticados.
    # Por ahora, la llamada proviene solamente desde un frontend de admin ya protegido.

    try:
        req_data = req.get_json(silent=True)
        if not req_data:
            return _add_cors_headers({"error": "Bad Request", "message": "Missing JSON body"}, 400)

        user_firestore_id = req_data.get('userFirestoreId')  # ID del documento del usuario a editar
        new_nombre_raw = req_data.get('newNombre')

        if not user_firestore_id or not new_nombre_raw:
            return _add_cors_headers({"error": "Bad Request", "message": "Missing userFirestoreId or newNombre"}, 400)

        new_nombre = new_nombre_raw.strip()  # Limpiar espacios al inicio y final

        if not (3 <= len(new_nombre) <= 80):  # Validar longitud del nuevo nombre
            return _add_cors_headers(
                {"error": "Bad Request", "message": "El nuevo nombre debe tener entre 3 y 80 caracteres."}, 400)

        user_doc_ref = db.collection('users').document(user_firestore_id)
        user_doc = user_doc_ref.get()

        if not user_doc.exists:
            return _add_cors_headers({"error": "Not Found", "message": "User not found"}, 404)

        user_data = user_doc.to_dict()
        current_usuario_id = user_data.get('usuarioId')
        new_usuario_id_generado = current_usuario_id  # Por defecto, mantener el actual

        # Solo proceder a cambiar usuarioId si el nombre base ha cambiado
        # y si el usuarioId actual tiene el formato esperado "nombrebase-numero"
        current_nombre_base_original = current_usuario_id.split('-')[0] if current_usuario_id and '-' in current_usuario_id else ""

        new_nombre_limpio_base = re.sub(r'\W+', '', new_nombre.lower().split(' ')[0])
        if not new_nombre_limpio_base:
            new_nombre_limpio_base = "usuario"  # Fallback

        if current_usuario_id and '-' in current_usuario_id and new_nombre_limpio_base != current_nombre_base_original:
            id_parts = current_usuario_id.split('-')
            numeric_part = id_parts[-1]  # Tomar la última parte después del último guion

            is_numeric_part_valid = False
            try:
                int(numeric_part)  # Verificar si es realmente un número
                is_numeric_part_valid = True
            except ValueError:
                print(
                    f"WARNING: Numeric part '{numeric_part}' from usuarioId '{current_usuario_id}' is not a valid integer for user {user_firestore_id}.")
                # Si no es un número válido, no intentamos reconstruir el usuarioId para evitar corrupción.
                # Se podría optar por generar un error o simplemente no cambiar el usuarioId.

            if is_numeric_part_valid:
                temp_new_usuario_id = f"{new_nombre_limpio_base}-{numeric_part}"

                # Verificar si el NUEVO usuarioId generado ya existe (excluyendo el usuario actual)
                users_ref = db.collection('users')
                existing_user_query = users_ref.where('usuarioId', '==', temp_new_usuario_id).limit(1).stream()
                conflicting_user = next(existing_user_query, None)

                if conflicting_user and conflicting_user.id != user_firestore_id:
                    return _add_cors_headers({"error": "Conflict",
                                              "message": f"El UsuarioID '{temp_new_usuario_id}' generado a partir del nuevo nombre ya existe para otro usuario."},
                                             409)
                else:
                    new_usuario_id_generado = temp_new_usuario_id  # Asignar el nuevo ID si no hay conflicto
            else:
                # Mantener el new_usuario_id_generado como current_usuario_id si la parte numérica no es válida
                print(
                    f"INFO: Keeping usuarioId as '{current_usuario_id}' because numeric part was not valid or base name did not change significantly.")

        # Preparar datos para la actualización
        update_data = {'nombre': new_nombre}
        if new_usuario_id_generado != current_usuario_id:  # Solo incluir usuarioId si realmente cambió
            update_data['usuarioId'] = new_usuario_id_generado

        user_doc_ref.update(update_data)

        response_message = f"Usuario actualizado. Nombre: {new_nombre}"
        if new_usuario_id_generado != current_usuario_id:
            response_message += f", UsuarioID: {new_usuario_id_generado}"
        else:
            response_message += f" (UsuarioID sin cambios: {current_usuario_id})"

        return _add_cors_headers({
            "message": response_message,
            "updatedNombre": new_nombre,
            "updatedUsuarioId": new_usuario_id_generado
        }, 200)

    except Exception as e:
        print(f"ERROR in updateUserFromAdmin: {e}")
        import traceback;
        traceback.print_exc()
        return _add_cors_headers({"error": "Internal Server Error", "message": str(e)}, 500)
