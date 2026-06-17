from Crypto.Cipher import AES
from Crypto.Util.Padding import pad, unpad
import base64
import os
import hashlib


def get_key(base64_flg=None):
    if base64_flg:
        return base64_encode(os.urandom(32))  # 32 bytes key
    else:
        return os.urandom(32)  # 32 bytes key


def get_iv(base64_flg=None):
    if base64_flg:
        return base64_encode(os.urandom(16))  # 16 bytes key
    else:
        return os.urandom(16)  # 16 bytes key


def aes_encrypt(data, key, iv, base64_flg=None):
    if isinstance(data, str):
        data = data.encode('utf-8')  # Convert string data to bytes
    cipher = AES.new(key, AES.MODE_CBC, iv)
    encrypted_data = cipher.encrypt(pad(data, AES.block_size))
    if base64_flg:
        encrypted_data = base64_encode(encrypted_data)
    return encrypted_data


def aes_decrypt(encrypted_data, key, iv, base64_flg=None):
    cipher = AES.new(key, AES.MODE_CBC, iv)
    if base64_flg:
        encrypted_data = base64_decode(encrypted_data)  # 16 bytes key
    decrypted_data = unpad(cipher.decrypt(encrypted_data), AES.block_size)
    return decrypted_data.decode('utf-8')  # Convert bytes data to string


def aes_encrypt_in_chunks(data, key, iv, chunk_size=4096):
    if isinstance(data, str):
        data = data.encode('utf-8')  # Convert string data to bytes
    cipher = AES.new(key, AES.MODE_CBC, iv)
    encrypted_data = b''

    for i in range(0, len(data), chunk_size):
        chunk = data[i:i+chunk_size]
        encrypted_data += cipher.encrypt(pad(chunk, AES.block_size))

    return encrypted_data


def aes_decrypt_in_chunks(data, key, iv, chunk_size=4096):
    cipher = AES.new(key, AES.MODE_CBC, iv)
    decrypted_data = b''

    for i in range(0, len(data), chunk_size):
        chunk = data[i:i+chunk_size]
        decrypted_data += cipher.decrypt(chunk)

    return unpad(decrypted_data, AES.block_size)


def base64_encode(data):
    return base64.b64encode(data).decode('utf-8')


def base64_decode(encoded_data):
    return base64.b64decode(encoded_data)


def encrypt_file(input_path, output_path, key, iv, chunk_size=4096):
    with open(input_path, 'rb') as f:
        data = f.read()

    encrypted_data = aes_encrypt_in_chunks(data, key, iv, chunk_size)

    with open(output_path, 'wb') as f:
        f.write(encrypted_data)


def decrypt_file(input_path, output_path, key, iv, chunk_size=4096):
    with open(input_path, 'rb') as f:
        data = f.read()

    decrypted_data = aes_decrypt_in_chunks(data, key, iv, chunk_size)

    with open(output_path, 'wb') as f:
        f.write(decrypted_data)


def hash_password(password: str) -> str:
    # Password hashing example (SHA-256)
    return hashlib.sha256(password.encode()).hexdigest()
