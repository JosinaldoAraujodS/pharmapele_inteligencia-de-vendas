import sys
import os

# Adiciona o diretório do backend ao sys.path para garantir que imports internos de main.py funcionem
sys.path.append(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "backend"))

from main import app
