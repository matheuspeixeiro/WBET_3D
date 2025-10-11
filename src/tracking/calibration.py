# src/tracking/calibration.py
# Módulo para gerenciar perfis de calibração (salvar, carregar, listar).

import json
import os
from datetime import datetime

# O diretório onde os perfis serão salvos
PROFILES_DIR = "profiles"


def ensure_profiles_dir():
    """Garante que o diretório de perfis exista."""
    os.makedirs(PROFILES_DIR, exist_ok=True)


def save_profile(profile_name: str, calibration_data: dict):
    """
    Salva os dados de calibração em um arquivo JSON com o nome do perfil.

    Args:
        profile_name (str): O nome do perfil (ex: "Matheus - Casa, Mesa de Jantar").
        calibration_data (dict): O dicionário contendo todos os dados da calibração.

    Returns:
        str: O caminho do arquivo salvo, ou None em caso de erro.
    """
    ensure_profiles_dir()
    # Limpa o nome do arquivo para evitar caracteres inválidos
    safe_filename = "".join(c for c in profile_name if c.isalnum() or c in (' ', '-', '_')).rstrip()
    filepath = os.path.join(PROFILES_DIR, f"{safe_filename}.json")

    try:
        with open(filepath, "w", encoding="utf-8") as f:
            json.dump(calibration_data, f, indent=4)
        print(f"Perfil '{profile_name}' salvo em {filepath}")
        return filepath
    except Exception as e:
        print(f"ERRO ao salvar o perfil '{profile_name}': {e}")
        return None


def load_profile(profile_name: str):
    """
    Carrega os dados de calibração de um arquivo de perfil.

    Args:
        profile_name (str): O nome do perfil a ser carregado.

    Returns:
        dict: Os dados de calibração, ou None se o arquivo não for encontrado ou for inválido.
    """
    ensure_profiles_dir()
    safe_filename = "".join(c for c in profile_name if c.isalnum() or c in (' ', '-', '_')).rstrip()
    filepath = os.path.join(PROFILES_DIR, f"{safe_filename}.json")

    if not os.path.exists(filepath):
        print(f"ERRO: Perfil '{profile_name}' não encontrado.")
        return None

    try:
        with open(filepath, "r", encoding="utf-8") as f:
            data = json.load(f)
        print(f"Perfil '{profile_name}' carregado de {filepath}")
        return data
    except Exception as e:
        print(f"ERRO ao carregar o perfil '{profile_name}': {e}")
        return None


def list_profiles():
    """
    Lista todos os perfis de calibração disponíveis no diretório de perfis.

    Returns:
        list: Uma lista com os nomes dos perfis encontrados.
    """
    ensure_profiles_dir()
    profiles = []
    for filename in os.listdir(PROFILES_DIR):
        if filename.endswith(".json"):
            # Remove a extensão .json para obter o nome do perfil
            profiles.append(os.path.splitext(filename)[0])
    return profiles