import firebase_admin
from firebase_admin import firestore
from google.cloud.firestore_v1.client import Client as FirestoreClient
from google.cloud.firestore_v1.transaction import Transaction, transactional
from flask import Flask, request, jsonify # Flask solo para jsonify si no se usa como router
from firebase_functions import https_fn
import re # Para limpiar nombre

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

# --- Cloud Function: registerUser ---
@https_fn.on_request()
def registerUser(req: https_fn.Request) -> https_fn.Response:
    """Registra un nuevo usuario generando un UsuarioID único."""
    if db is None: return https_fn.Response("Server Error: Firebase not initialized", status=500)

    try:
        req_data = req.get_json(silent=True)
        if not req_data: return https_fn.Response("Bad Request: Missing JSON body", status=400)

        cedula = req_data.get('cedula')
        nombre = req_data.get('nombre')
        apellido = req_data.get('apellido')

        if not all([cedula, nombre, apellido]):
            return https_fn.Response("Bad Request: Missing required fields (cedula, nombre, apellido)", status=400)

        # Limpiar nombre para usar en UsuarioID (solo letras y números del primer nombre)
        nombre_limpio = re.sub(r'\W+', '', nombre.lower().strip().split(' ')[0])
        if not nombre_limpio: nombre_limpio = "usuario" # Fallback

        users_ref = db.collection('users')

        # 1. Verificar si la Cédula ya existe (Query) - ¡Importante para evitar duplicados!
        cedula_query = users_ref.where('cedula', '==', cedula).limit(1).stream()
        if next(cedula_query, None):
            return https_fn.Response(f"Conflict: User with Cedula {cedula} already exists", status=409)

        # 2. Obtener el siguiente número (transaccional)
        counter_doc_ref = db.collection('counters').document('user_counter')
        try:
            next_number = get_next_user_number(db.transaction(), counter_doc_ref)
        except Exception as counter_error:
             print(f"ERROR getting next user number: {counter_error}")
             return https_fn.Response("Internal Server Error: Could not generate user number", status=500)

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
        # Usar jsonify de Flask para convertir dict a JSON correctamente
        return https_fn.Response(response=jsonify(response_body).data, status=201, headers={"Content-Type": "application/json"})

    except Exception as e:
        print(f"ERROR in registerUser: {e}")
        # import traceback; print(traceback.format_exc()) # Para debug detallado
        return https_fn.Response("Internal Server Error", status=500)

# --- Cloud Function: loginUser ---
@https_fn.on_request()
def loginUser(req: https_fn.Request) -> https_fn.Response:
    """Autentica un usuario buscando su **usuarioId**."""
    if db is None: return https_fn.Response("Server Error: Firebase not initialized", status=500)

    try:
        req_data = req.get_json(silent=True)
        if not req_data: return https_fn.Response("Bad Request: Missing JSON body", status=400)

        # ** El usuario envía 'usuarioId' para login **
        usuario_id_login = req_data.get('usuarioId')
        if not usuario_id_login:
            return https_fn.Response("Bad Request: Missing usuarioId field for login", status=400)

        users_ref = db.collection('users')
        # Buscar el usuario por el campo 'usuarioId'
        query = users_ref.where('usuarioId', '==', usuario_id_login).limit(1)
        results = query.stream()
        user_doc = next(results, None) # Obtiene el primer documento que coincida o None

        if not user_doc:
            return https_fn.Response(f"Not Found: User with UsuarioID '{usuario_id_login}' not found", status=404)

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
        # Usar jsonify de Flask para convertir dict a JSON correctamente
        return https_fn.Response(response=jsonify(response_body).data, status=200, headers={"Content-Type": "application/json"})

    except Exception as e:
        print(f"ERROR in loginUser: {e}")
        # import traceback; print(traceback.format_exc())
        return https_fn.Response("Internal Server Error", status=500)
