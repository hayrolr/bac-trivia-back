# generate_password_hash.py
import bcrypt
import getpass

def generate_hash():
    password = getpass.getpass("Ingresa la contraseña para el admin: ")
    hashed_password = bcrypt.hashpw(password.encode('utf-8'), bcrypt.gensalt())
    print(f"Admin ID (ej: admin1): [agrega el adminId que usarás en Firestore]")
    print(f"Hashed Password (guardar en Firestore): {hashed_password.decode('utf-8')}")

if __name__ == "__main__":
    generate_hash()
    # Para usarlo: python generate_password_hash.py
    # Luego se debe copiar el hash generado.
