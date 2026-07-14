"""
PHARMAPELE - Backend API
FastAPI + PostgreSQL (Multi-lojas & Conectividade Melhorada)
"""

from fastapi import FastAPI, UploadFile, File, HTTPException, Depends
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse, Response
import csv
import io
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from pydantic import BaseModel
import bcrypt
import jwt
import psycopg2
import psycopg2.extras
from psycopg2.pool import ThreadedConnectionPool
from contextlib import contextmanager
import xml.etree.ElementTree as ET
from datetime import datetime, date
import os
from typing import Optional, List

# ─── Security Config & Helpers ─────────────────────────────
JWT_SECRET = os.getenv("JWT_SECRET", "super-secret-pharmapele-key-123456")
JWT_ALGORITHM = "HS256"

security = HTTPBearer()

def verify_password(plain_password: str, hashed_password: str) -> bool:
    try:
        return bcrypt.checkpw(plain_password.encode('utf-8'), hashed_password.encode('utf-8'))
    except Exception:
        return False

def hash_password(password: str) -> str:
    return bcrypt.hashpw(password.encode('utf-8'), bcrypt.gensalt()).decode('utf-8')

# ─── Config ────────────────────────────────────────────────
DB_CONFIG = {
    "host": os.getenv("DB_HOST", "localhost"),
    "port": os.getenv("DB_PORT", "5432"),
    "database": os.getenv("DB_NAME", "pharmapele"),
    "user": os.getenv("DB_USER", "postgres"),
    "password": os.getenv("DB_PASS", "Ojuara10*"),
}

# Determinar caminho absoluto do frontend (independente de onde a API é iniciada)
BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FRONTEND_DIR = os.path.join(BASE_DIR, "frontend")

NS = {"nfe": "http://www.portalfiscal.inf.br/nfe"}

CATEGORIAS_NCM = {
    "3401": "Sabonetes / Limpeza",
    "3304": "Cosméticos / Maquiagem",
    "3303": "Perfumes",
    "3305": "Cuidados Capilares",
    "3306": "Higiene Bucal",
    "3307": "Cuidados Pessoais",
    "3006": "Medicamentos",
    "3004": "Medicamentos",
    "3003": "Medicamentos",
    "4202": "Acessórios / Bolsas",
    "2106": "Suplementos",
}

FORMAS_PAGAMENTO = {
    "01": "Dinheiro",
    "02": "Cheque",
    "03": "Cartão de Crédito",
    "04": "Cartão de Débito",
    "05": "Crédito Loja",
    "10": "Vale Alimentação",
    "11": "Vale Refeição",
    "12": "Vale Presente",
    "13": "Vale Combustível",
    "15": "Boleto",
    "99": "Outros",
}

# ─── Connection Pool (psycopg2) ─────────────────────────────
try:
    db_pool = ThreadedConnectionPool(
        minconn=1,
        maxconn=20,
        **DB_CONFIG
    )
except Exception as e:
    print(f"[ERRO] Falha ao inicializar o Pool de Conexão com o Banco de Dados: {e}")
    raise e

# ─── App ────────────────────────────────────────────────────
app = FastAPI(title="Pharmapele API", version="1.1.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# Serve frontend estático se a pasta existir
if os.path.exists(FRONTEND_DIR):
    app.mount("/static", StaticFiles(directory=FRONTEND_DIR), name="static")


# ─── DB Context Manager ──────────────────────────────────────
@contextmanager
def get_db():
    conn = db_pool.getconn()
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        db_pool.putconn(conn)


def query(sql, params=None, fetchall=True):
    with get_db() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(sql, params)
            if fetchall:
                return cur.fetchall()
            return cur.fetchone()


def execute(sql, params=None):
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute(sql, params)
            if cur.description:
                return cur.fetchone()
            return None


def generate_jwt(user_id: int, email: str, nivel_acesso: str, franquia_id: Optional[int], loja_id: Optional[int]) -> str:
    # Obter lista de franquias associadas (muitos-para-muitos)
    franquias_ids = []
    try:
        rows = query("""
            SELECT franquia_id 
            FROM usuario_franquias 
            WHERE usuario_id = %s
        """, (user_id,))
        franquias_ids = [r["franquia_id"] for r in rows]
    except Exception as e:
        print(f"Erro ao buscar franquias no token: {e}")

    payload = {
        "sub": str(user_id),
        "email": email,
        "nivel_acesso": nivel_acesso,
        "franquia_id": franquia_id,  # Franquia ativa principal
        "loja_id": loja_id,          # Loja ativa principal (para operadores)
        "franquias": franquias_ids,  # Lista de todas as franquias permitidas
        "exp": datetime.utcnow().timestamp() + (24 * 3600)  # Expira em 24h
    }
    return jwt.encode(payload, JWT_SECRET, algorithm=JWT_ALGORITHM)


def get_current_user(credentials: HTTPAuthorizationCredentials = Depends(security)) -> dict:
    token = credentials.credentials
    try:
        payload = jwt.decode(token, JWT_SECRET, algorithms=[JWT_ALGORITHM])
        return {
            "id": int(payload["sub"]),
            "email": payload["email"],
            "nivel_acesso": payload["nivel_acesso"],
            "franquia_id": payload["franquia_id"],
            "loja_id": payload["loja_id"],
            "franquias": payload["franquias"]
        }
    except jwt.ExpiredSignatureError:
        raise HTTPException(status_code=401, detail="Token de acesso expirado")
    except jwt.PyJWTError:
        raise HTTPException(status_code=401, detail="Token de acesso inválido")


def aplicar_filtro_segurança(current_user: dict, loja_id_param: Optional[int] = None) -> list[int]:
    """
    Retorna uma lista de IDs de lojas permitidas para o usuário logado, 
    respeitando o filtro de loja_id_param se especificado.
    """
    with get_db() as conn:
        with conn.cursor() as cur:
            if current_user["nivel_acesso"] == "master":
                # Master pode ver qualquer loja. Se especificou loja_id_param, retorna ela.
                if loja_id_param and loja_id_param > 0:
                    return [loja_id_param]
                # Caso contrário, retorna todas as lojas ativas
                cur.execute("SELECT id FROM lojas WHERE ativo = TRUE")
                return [r[0] for r in cur.fetchall()]
                
            elif current_user["nivel_acesso"] == "franqueado":
                # Franqueado pode ver lojas das suas franquias.
                cur.execute("""
                    SELECT id FROM lojas 
                    WHERE ativo = TRUE AND franquia_id = ANY(%s)
                """, (current_user["franquias"],))
                lojas_permitidas = [r[0] for r in cur.fetchall()]
                
                if loja_id_param and loja_id_param > 0:
                    if loja_id_param in lojas_permitidas:
                        return [loja_id_param]
                    else:
                        # Se tentou ver uma loja não permitida, retorna lista vazia
                        return []
                return lojas_permitidas
                
            else: # operador
                # Operador pode ver apenas a sua loja_id atribuída.
                loja_op = current_user["loja_id"]
                if loja_id_param and loja_id_param > 0:
                    if loja_id_param == loja_op:
                        return [loja_op]
                    else:
                        return []
                return [loja_op] if loja_op else []


def _require_master(current_user: dict) -> None:
    if current_user["nivel_acesso"] != "master":
        raise HTTPException(status_code=403, detail="Acesso restrito à matriz (master)")


def _require_admin(current_user: dict) -> None:
    if current_user["nivel_acesso"] not in ("master", "franqueado"):
        raise HTTPException(status_code=403, detail="Acesso restrito à administração")


def _lojas_gestao_ids(current_user: dict) -> list[int]:
    return aplicar_filtro_segurança(current_user, None)


def _validar_loja_gestao(current_user: dict, loja_id: Optional[int]) -> None:
    if not loja_id:
        raise HTTPException(status_code=400, detail="Loja é obrigatória para operador")
    if loja_id not in _lojas_gestao_ids(current_user):
        raise HTTPException(status_code=403, detail="Loja fora do seu escopo de gestão")


def _validar_franquias_gestao(current_user: dict, franquia_ids: list[int]) -> None:
    if current_user["nivel_acesso"] == "master":
        return
    permitidas = set(current_user.get("franquias") or [])
    if not franquia_ids or not set(franquia_ids).issubset(permitidas):
        raise HTTPException(status_code=403, detail="Franquia fora do seu escopo de gestão")


def _usuario_pode_gestao(current_user: dict, usuario_id: int) -> dict:
    row = query("""
        SELECT u.id, u.nome, u.email, u.nivel_acesso, u.ativo, u.loja_id, u.franquia_id
        FROM usuarios u
        WHERE u.id = %s
    """, (usuario_id,), fetchall=False)
    if not row:
        raise HTTPException(status_code=404, detail="Usuário não encontrado")
    if current_user["nivel_acesso"] == "master":
        return dict(row)
    if row["nivel_acesso"] != "operador":
        raise HTTPException(status_code=403, detail="Franqueado só pode gerenciar operadores")
    if row["loja_id"] not in _lojas_gestao_ids(current_user):
        raise HTTPException(status_code=403, detail="Usuário fora do seu escopo de gestão")
    return dict(row)


def _sync_usuario_franquias(usuario_id: int, franquia_ids: list[int]) -> None:
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute("DELETE FROM usuario_franquias WHERE usuario_id = %s", (usuario_id,))
            for fid in franquia_ids:
                cur.execute(
                    "INSERT INTO usuario_franquias (usuario_id, franquia_id) VALUES (%s, %s) ON CONFLICT DO NOTHING",
                    (usuario_id, fid),
                )
            cur.execute(
                "UPDATE usuarios SET franquia_id = %s, atualizado_em = NOW() WHERE id = %s",
                (franquia_ids[0] if franquia_ids else None, usuario_id),
            )


def clausula_periodo(data_inicio: Optional[str], data_fim: Optional[str], coluna: str = "data_emissao") -> tuple:
    """Retorna fragmento SQL (AND ...) e parâmetros para filtro de período."""
    partes = []
    params = []
    if data_inicio:
        partes.append(f"{coluna} >= %s::date")
        params.append(data_inicio)
    if data_fim:
        partes.append(f"{coluna} < %s::date + INTERVAL '1 day'")
        params.append(data_fim)
    if not partes:
        return "", []
    return " AND " + " AND ".join(partes), params


# ─── Auto Migration ─────────────────────────────────────────
def executar_migracoes():
    # Caminhos para os scripts de migração
    caminho_sql_02 = "../sql/02_adicionar_lojas.sql"
    if not os.path.exists(caminho_sql_02):
        caminho_sql_02 = "sql/02_adicionar_lojas.sql"

    caminho_sql_03 = "../sql/03_suporte_franquias.sql"
    if not os.path.exists(caminho_sql_03):
        caminho_sql_03 = "sql/03_suporte_franquias.sql"

    caminho_sql_04 = "../sql/04_many_to_many_franquias.sql"
    if not os.path.exists(caminho_sql_04):
        caminho_sql_04 = "sql/04_many_to_many_franquias.sql"

    try:
        with get_db() as conn:
            with conn.cursor() as cur:
                # 1. Verificar/Executar Migração 02 (Múltiplas Lojas)
                cur.execute("SELECT EXISTS (SELECT FROM pg_tables WHERE schemaname = 'public' AND tablename = 'lojas')")
                lojas_exists = cur.fetchone()[0]
                
                loja_id_exists = False
                if lojas_exists:
                    cur.execute("SELECT EXISTS (SELECT FROM information_schema.columns WHERE table_name='notas_fiscais' AND column_name='loja_id')")
                    loja_id_exists = cur.fetchone()[0]
                
                if not lojas_exists or not loja_id_exists:
                    if os.path.exists(caminho_sql_02):
                        print(f"[MIGRAÇÃO] Executando o script de migração: {caminho_sql_02} ...")
                        with open(caminho_sql_02, "r", encoding="utf-8") as f:
                            sql_content = f.read()
                        cur.execute(sql_content)
                        conn.commit()
                        print("[MIGRAÇÃO] Banco de dados migrado com sucesso para suporte a múltiplas lojas!")
                    else:
                        print(f"[MIGRAÇÃO] Erro: Script de migração não encontrado em {caminho_sql_02}")
                
                # 2. Verificar/Executar Migração 03 (Suporte a Franquias e Autenticação)
                cur.execute("SELECT EXISTS (SELECT FROM pg_tables WHERE schemaname = 'public' AND tablename = 'franquias')")
                franquias_exists = cur.fetchone()[0]
                
                cur.execute("SELECT EXISTS (SELECT FROM pg_tables WHERE schemaname = 'public' AND tablename = 'usuarios')")
                usuarios_exists = cur.fetchone()[0]
                
                if not franquias_exists or not usuarios_exists:
                    if os.path.exists(caminho_sql_03):
                        print(f"[MIGRAÇÃO] Executando o script de migração: {caminho_sql_03} ...")
                        with open(caminho_sql_03, "r", encoding="utf-8") as f:
                            sql_content = f.read()
                        cur.execute(sql_content)
                        conn.commit()
                        print("[MIGRAÇÃO] Banco de dados migrado com sucesso para suporte a franquias e usuários!")
                    else:
                        print(f"[MIGRAÇÃO] Erro: Script de migração não encontrado em {caminho_sql_03}")
                else:
                    print("[MIGRAÇÃO] Banco de dados já possui suporte a franquias e usuários.")

                # 3. Verificar/Executar Migração 04 (Mapeamento Muitos-para-Muitos Usuários e Franquias)
                cur.execute("SELECT EXISTS (SELECT FROM pg_tables WHERE schemaname = 'public' AND tablename = 'usuario_franquias')")
                usuario_franquias_exists = cur.fetchone()[0]

                if not usuario_franquias_exists:
                    if os.path.exists(caminho_sql_04):
                        print(f"[MIGRAÇÃO] Executando o script de migração: {caminho_sql_04} ...")
                        with open(caminho_sql_04, "r", encoding="utf-8") as f:
                            sql_content = f.read()
                        cur.execute(sql_content)
                        conn.commit()
                        print("[MIGRAÇÃO] Banco de dados migrado com sucesso para suporte a múltiplos franqueados muitos-para-muitos!")
                    else:
                        print(f"[MIGRAÇÃO] Erro: Script de migração não encontrado em {caminho_sql_04}")
                else:
                    print("[MIGRAÇÃO] Banco de dados já possui suporte a mapeamento muitos-para-muitos de franquias.")

                # 4. Criar outros perfis de teste (franqueado e operador) se não existirem
                cur.execute("SELECT 1 FROM usuarios WHERE email = 'franqueado@pharmapele.com.br'")
                franqueado_exists = cur.fetchone()
                if not franqueado_exists:
                    cur.execute("""
                        INSERT INTO usuarios (nome, email, senha_hash, nivel_acesso, franquia_id, ativo)
                        VALUES ('Franqueado Teste', 'franqueado@pharmapele.com.br', 
                                '$2b$12$X1/avgCxABbjZcTi3EHRxuZJaxWyvY9TZ7krjNunwfR6jnwkVPMW.', 'franqueado', 1, TRUE)
                        RETURNING id
                    """)
                    franqueado_id = cur.fetchone()[0]
                    # Associar na tabela muitos-para-muitos
                    cur.execute("""
                        INSERT INTO usuario_franquias (usuario_id, franquia_id)
                        VALUES (%s, 1)
                    """, (franqueado_id,))
                    print("[MIGRAÇÃO] Usuário franqueado@pharmapele.com.br cadastrado com sucesso.")

                cur.execute("SELECT 1 FROM usuarios WHERE email = 'operador@pharmapele.com.br'")
                operador_exists = cur.fetchone()
                if not operador_exists:
                    # Obter o id da primeira loja
                    cur.execute("SELECT id FROM lojas LIMIT 1")
                    loja_row = cur.fetchone()
                    if loja_row:
                        loja_id = loja_row[0]
                        cur.execute("""
                            INSERT INTO usuarios (nome, email, senha_hash, nivel_acesso, loja_id, ativo)
                            VALUES ('Operador Teste', 'operador@pharmapele.com.br', 
                                    '$2b$12$X1/avgCxABbjZcTi3EHRxuZJaxWyvY9TZ7krjNunwfR6jnwkVPMW.', 'operador', %s, TRUE)
                        """, (loja_id,))
                        print("[MIGRAÇÃO] Usuário operador@pharmapele.com.br cadastrado com sucesso.")
                conn.commit()
    except Exception as e:
        print(f"[MIGRAÇÃO] Erro ao aplicar migrações: {e}")


@app.on_event("startup")
def startup_event():
    # Ignora as migrações automáticas em ambiente Serverless ou se explicitado por variável de ambiente
    if os.getenv("SKIP_MIGRATIONS") == "true" or os.getenv("VERCEL") is not None:
        print("[MIGRAÇÃO] Ignorando migrações automáticas de banco de dados (ambiente Serverless/Vercel).")
        return
    print("Iniciando verificação de banco de dados...")
    executar_migracoes()


@app.on_event("shutdown")
def shutdown_event():
    db_pool.closeall()
    print("Pool de conexões com o banco de dados fechado.")


# ─── XML Parser ─────────────────────────────────────────────
def get_ncm_categoria(ncm: str) -> str:
    if not ncm:
        return "Outros"
    for prefix, cat in CATEGORIAS_NCM.items():
        if ncm.startswith(prefix):
            return cat
    return "Outros"


def parse_nfce_xml(xml_content: bytes, filename: str = "") -> dict:
    root = ET.fromstring(xml_content)

    nfe = root.find(".//nfe:infNFe", NS)
    if nfe is None:
        raise ValueError("XML não é uma NFC-e válida")

    chave = nfe.get("Id", "").replace("NFe", "")

    # Emissão
    ide = nfe.find("nfe:ide", NS)
    data_emissao_str = ide.findtext("nfe:dhEmi", "", NS)
    data_emissao = datetime.fromisoformat(data_emissao_str)
    numero_nf = ide.findtext("nfe:nNF", "", NS)
    serie = ide.findtext("nfe:serie", "", NS)

    # Emitente (Loja)
    emit = nfe.find("nfe:emit", NS)
    if emit is None:
        raise ValueError("XML não contém a tag <emit> com dados do emitente")
    
    cnpj_loja = emit.findtext("nfe:CNPJ", "", NS)
    if not cnpj_loja:
        raise ValueError("XML não contém CNPJ do emitente")
        
    razao_social_loja = emit.findtext("nfe:xNome", "", NS)
    nome_fantasia_loja = emit.findtext("nfe:xFant", "", NS) or razao_social_loja
    
    # Padronização de marca (Pharmapel -> Pharmapele)
    def limpar_nome_marca(nome: str) -> str:
        if not nome:
            return ""
        import re
        nome = re.sub(r'\bPHARMAPEL\b', 'PHARMAPELE', nome)
        nome = re.sub(r'\bPharmapel\b', 'Pharmapele', nome)
        nome = re.sub(r'(?i)\bpharmapel\b', 'Pharmapele', nome)
        nome = re.sub(r'\bPHARMAPELEE\b', 'PHARMAPELE', nome)
        nome = re.sub(r'\bPharmapelee\b', 'Pharmapele', nome)
        nome = re.sub(r'(?i)\bpharmapelee\b', 'Pharmapele', nome)
        return nome

    razao_social_loja = limpar_nome_marca(razao_social_loja)
    nome_fantasia_loja = limpar_nome_marca(nome_fantasia_loja)
    
    # Endereço da loja
    ender_emit = emit.find("nfe:enderEmit", NS)
    if ender_emit is not None:
        logr = ender_emit.findtext("nfe:xLgr", "", NS)
        nro = ender_emit.findtext("nfe:nro", "", NS)
        bairro = ender_emit.findtext("nfe:xBairro", "", NS)
        endereco_loja = f"{logr}, {nro} - {bairro}"
        municipio_loja = ender_emit.findtext("nfe:xMun", "", NS)
        uf_loja = ender_emit.findtext("nfe:UF", "", NS)
        telefone_loja = ender_emit.findtext("nfe:fone", "", NS)
    else:
        endereco_loja, municipio_loja, uf_loja, telefone_loja = "", "", "", ""

    # Destinatário (cliente) - Opcional em NFC-e
    dest = nfe.find("nfe:dest", NS)
    cliente = None
    if dest is not None:
        cpf = dest.findtext("nfe:CPF", "", NS)
        nome = dest.findtext("nfe:xNome", "", NS)
        
        # Só processa dados detalhados do cliente se houver CPF
        if cpf:
            end = dest.find("nfe:enderDest", NS)
            telefone = end.findtext("nfe:fone", "", NS) if end else ""
            cep = end.findtext("nfe:CEP", "", NS) if end else ""
            municipio = end.findtext("nfe:xMun", "", NS) if end else ""
            uf = end.findtext("nfe:UF", "", NS) if end else ""
            
            cliente = {
                "cpf": cpf,
                "nome": nome,
                "telefone": telefone,
                "cep": cep,
                "municipio": municipio,
                "uf": uf,
            }

    # Totais
    tot = nfe.find(".//nfe:ICMSTot", NS)
    v_prod = float(tot.findtext("nfe:vProd", "0", NS))
    v_desc = float(tot.findtext("nfe:vDesc", "0", NS))
    v_nf = float(tot.findtext("nfe:vNF", "0", NS))

    # Pagamento
    t_pag = nfe.findtext(".//nfe:tPag", "99", NS)
    forma_pag = FORMAS_PAGAMENTO.get(t_pag, "Outros")

    # Itens — agrupa por código de produto (NFC-e às vezes repete o item)
    itens_map = {}
    for det in nfe.findall("nfe:det", NS):
        prod = det.find("nfe:prod", NS)
        cod = prod.findtext("nfe:cProd", "", NS)
        nome_prod = prod.findtext("nfe:xProd", "", NS)
        ncm = prod.findtext("nfe:NCM", "", NS)
        unidade = prod.findtext("nfe:uCom", "UN", NS)
        qtd = float(prod.findtext("nfe:qCom", "1", NS))
        v_unit = float(prod.findtext("nfe:vUnCom", "0", NS))
        v_item_desc = float(prod.findtext("nfe:vDesc", "0", NS))
        v_item_total = float(prod.findtext("nfe:vProd", "0", NS))

        if cod in itens_map:
            itens_map[cod]["quantidade"] += qtd
            itens_map[cod]["valor_desconto"] += v_item_desc
            itens_map[cod]["valor_total"] += v_item_total - v_item_desc
        else:
            itens_map[cod] = {
                "codigo": cod,
                "nome": nome_prod,
                "ncm": ncm,
                "categoria": get_ncm_categoria(ncm),
                "unidade": unidade,
                "quantidade": qtd,
                "valor_unitario": v_unit,
                "valor_desconto": v_item_desc,
                "valor_total": v_item_total - v_item_desc,
            }

    return {
        "chave": chave,
        "numero_nf": numero_nf,
        "serie": serie,
        "data_emissao": data_emissao,
        "loja": {
            "cnpj": cnpj_loja,
            "razao_social": razao_social_loja,
            "nome_fantasia": nome_fantasia_loja,
            "endereco": endereco_loja,
            "municipio": municipio_loja,
            "uf": uf_loja,
            "telefone": telefone_loja,
        },
        "cliente": cliente,
        "valor_produtos": v_prod,
        "valor_desconto": v_desc,
        "valor_total": v_nf,
        "forma_pagamento": forma_pag,
        "xml_filename": filename,
        "itens": list(itens_map.values()),
    }


def salvar_nfce(nfce: dict) -> dict:
    with get_db() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            # Verificar duplicata
            cur.execute("SELECT id FROM notas_fiscais WHERE chave_nfe = %s", (nfce["chave"],))
            if cur.fetchone():
                return {"status": "duplicada", "chave": nfce["chave"]}

            # Upsert loja
            l = nfce["loja"]
            cur.execute("""
                INSERT INTO lojas (cnpj, nome_fantasia, razao_social, endereco, municipio, uf, telefone)
                VALUES (%s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT (cnpj) DO UPDATE SET
                    nome_fantasia = EXCLUDED.nome_fantasia,
                    razao_social = EXCLUDED.razao_social,
                    endereco = EXCLUDED.endereco,
                    municipio = EXCLUDED.municipio,
                    uf = EXCLUDED.uf,
                    telefone = EXCLUDED.telefone,
                    atualizado_em = NOW()
                RETURNING id
            """, (l["cnpj"], l["nome_fantasia"], l["razao_social"], l["endereco"], l["municipio"], l["uf"], l["telefone"]))
            loja_id = cur.fetchone()["id"]

            # Upsert cliente (se identificado)
            cliente_id = None
            c = nfce["cliente"]
            if c and c.get("cpf"):
                cur.execute("""
                    INSERT INTO clientes (cpf, nome, telefone, cep, municipio, uf)
                    VALUES (%s, %s, %s, %s, %s, %s)
                    ON CONFLICT (cpf) DO UPDATE SET
                        nome = EXCLUDED.nome,
                        telefone = COALESCE(EXCLUDED.telefone, clientes.telefone),
                        atualizado_em = NOW()
                    RETURNING id
                """, (c["cpf"], c["nome"], c["telefone"], c["cep"], c["municipio"], c["uf"]))
                cliente_id = cur.fetchone()["id"]

            # Inserir NF
            cur.execute("""
                INSERT INTO notas_fiscais
                    (chave_nfe, numero_nf, serie, data_emissao, cliente_id, loja_id,
                     valor_produtos, valor_desconto, valor_total, forma_pagamento, xml_filename)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                RETURNING id
            """, (
                nfce["chave"], nfce["numero_nf"], nfce["serie"],
                nfce["data_emissao"], cliente_id, loja_id,
                nfce["valor_produtos"], nfce["valor_desconto"],
                nfce["valor_total"], nfce["forma_pagamento"], nfce["xml_filename"]
            ))
            nota_id = cur.fetchone()["id"]

            # Itens
            for item in nfce["itens"]:
                cur.execute("""
                    INSERT INTO produtos (codigo, nome, ncm, categoria, unidade, preco_atual, loja_id)
                    VALUES (%s, %s, %s, %s, %s, %s, %s)
                    ON CONFLICT (codigo, loja_id) DO UPDATE SET
                        nome = EXCLUDED.nome,
                        preco_atual = EXCLUDED.preco_atual,
                        atualizado_em = NOW()
                    RETURNING id
                """, (
                    item["codigo"], item["nome"], item["ncm"],
                    item["categoria"], item["unidade"], item["valor_unitario"], loja_id
                ))
                prod_id = cur.fetchone()["id"]

                cur.execute("""
                    INSERT INTO itens_venda
                        (nota_id, produto_id, quantidade, valor_unitario, valor_desconto, valor_total)
                    VALUES (%s, %s, %s, %s, %s, %s)
                """, (nota_id, prod_id, item["quantidade"], item["valor_unitario"],
                      item["valor_desconto"], item["valor_total"]))

        conn.commit()
    
    nome_cliente = c["nome"] if c else "Consumidor Não Identificado"
    return {"status": "ok", "nota_id": nota_id, "cliente": nome_cliente, "loja": l["nome_fantasia"]}


# ─── Rotas ──────────────────────────────────────────────────

@app.get("/")
def root():
    index_path = os.path.join(FRONTEND_DIR, "index.html")
    if os.path.exists(index_path):
        return FileResponse(index_path)
    return {"msg": "Pharmapele API rodando"}


@app.get("/favicon.ico", include_in_schema=False)
def favicon():
    favicon_path = os.path.join(BASE_DIR, "pharmapele.png")
    if os.path.exists(favicon_path):
        return FileResponse(favicon_path)
    return Response(status_code=204)



class LoginRequest(BaseModel):
    email: str
    senha: str


class ClienteUpdate(BaseModel):
    nome: str
    telefone: Optional[str] = None


@app.post("/api/login")
def login(req: LoginRequest):
    row = query("""
        SELECT id, nome, email, senha_hash, nivel_acesso, franquia_id, loja_id, ativo 
        FROM usuarios 
        WHERE email = %s AND ativo = TRUE
    """, (req.email,), fetchall=False)
    
    if not row:
        raise HTTPException(status_code=401, detail="E-mail ou senha incorretos")
    
    # Verificar senha
    if not verify_password(req.senha, row["senha_hash"]):
        raise HTTPException(status_code=401, detail="E-mail ou senha incorretos")
        
    # Gerar Token JWT
    token = generate_jwt(
        user_id=row["id"],
        email=row["email"],
        nivel_acesso=row["nivel_acesso"],
        franquia_id=row["franquia_id"],
        loja_id=row["loja_id"]
    )
    
    return {
        "access_token": token,
        "token_type": "bearer",
        "usuario": {
            "nome": row["nome"],
            "email": row["email"],
            "nivel_acesso": row["nivel_acesso"]
        }
    }


@app.get("/api/lojas")
def listar_lojas(current_user: dict = Depends(get_current_user)):
    """Retorna as lojas ativas cadastradas com base no perfil do usuário"""
    if current_user["nivel_acesso"] == "master":
        rows = query("""
            SELECT id, cnpj, nome_fantasia, razao_social, municipio, uf, endereco 
            FROM lojas 
            WHERE ativo = TRUE 
            ORDER BY nome_fantasia
        """)
    elif current_user["nivel_acesso"] == "franqueado":
        rows = query("""
            SELECT id, cnpj, nome_fantasia, razao_social, municipio, uf, endereco 
            FROM lojas 
            WHERE ativo = TRUE AND franquia_id = ANY(%s) 
            ORDER BY nome_fantasia
        """, (current_user["franquias"],))
    else:  # operador
        rows = query("""
            SELECT id, cnpj, nome_fantasia, razao_social, municipio, uf, endereco 
            FROM lojas 
            WHERE ativo = TRUE AND id = %s 
            ORDER BY nome_fantasia
        """, (current_user["loja_id"],))
    return [dict(r) for r in rows]


@app.get("/api/usuario/franquias")
def listar_franquias_usuario(current_user: dict = Depends(get_current_user)):
    """Retorna as franquias associadas ao usuário logado"""
    if current_user["nivel_acesso"] == "master":
        rows = query("""
            SELECT id, nome, ativo 
            FROM franquias 
            WHERE ativo = TRUE 
            ORDER BY nome
        """)
    elif current_user["nivel_acesso"] == "franqueado":
        rows = query("""
            SELECT f.id, f.nome, f.ativo 
            FROM franquias f
            JOIN usuario_franquias uf ON f.id = uf.franquia_id
            WHERE uf.usuario_id = %s AND f.ativo = TRUE
            ORDER BY f.nome
        """, (current_user["id"],))
    else:  # operador
        rows = query("""
            SELECT f.id, f.nome, f.ativo 
            FROM franquias f
            JOIN lojas l ON f.id = l.franquia_id
            WHERE l.id = %s AND f.ativo = TRUE
        """, (current_user["loja_id"],))
    return [dict(r) for r in rows]


class SenhaPerfilUpdate(BaseModel):
    senha_nova: str


@app.get("/api/usuario/perfil")
def usuario_perfil(current_user: dict = Depends(get_current_user)):
    """Dados do usuário logado, vínculos e escopo de acesso."""
    row = query("""
        SELECT
            u.id, u.nome, u.email, u.nivel_acesso, u.ativo, u.loja_id, u.franquia_id,
            u.criado_em,
            l.nome_fantasia AS loja_nome,
            l.municipio AS loja_municipio,
            l.uf AS loja_uf,
            f.nome AS franquia_principal
        FROM usuarios u
        LEFT JOIN lojas l ON l.id = u.loja_id
        LEFT JOIN franquias f ON f.id = u.franquia_id
        WHERE u.id = %s
    """, (current_user["id"],), fetchall=False)
    if not row:
        raise HTTPException(status_code=404, detail="Usuário não encontrado")

    franquias = query("""
        SELECT f.id, f.nome
        FROM franquias f
        JOIN usuario_franquias uf ON uf.franquia_id = f.id
        WHERE uf.usuario_id = %s
        ORDER BY f.nome
    """, (current_user["id"],))

    lojas_escopo = aplicar_filtro_segurança(current_user, None)
    perfil_label = {
        "master": "Diretoria (Master)",
        "franqueado": "Franqueado",
        "operador": "Operador de Loja",
    }.get(row["nivel_acesso"], row["nivel_acesso"])

    return {
        "usuario": {
            "id": row["id"],
            "nome": row["nome"],
            "email": row["email"],
            "nivel_acesso": row["nivel_acesso"],
            "perfil_label": perfil_label,
            "ativo": row["ativo"],
            "criado_em": row["criado_em"].isoformat() if row["criado_em"] else None,
            "loja_id": row["loja_id"],
            "loja_nome": row["loja_nome"],
            "loja_municipio": row["loja_municipio"],
            "loja_uf": row["loja_uf"],
            "franquia_principal": row["franquia_principal"],
        },
        "franquias": [dict(r) for r in franquias],
        "lojas_no_escopo": len(lojas_escopo),
    }


@app.put("/api/usuario/perfil/senha")
def usuario_alterar_senha_perfil(
    body: SenhaPerfilUpdate,
    current_user: dict = Depends(get_current_user),
):
    """Altera a senha do próprio usuário logado (sessão autenticada)."""
    if len(body.senha_nova) < 6:
        raise HTTPException(status_code=400, detail="Nova senha deve ter ao menos 6 caracteres")
    row = query(
        "SELECT id FROM usuarios WHERE id = %s AND ativo = TRUE",
        (current_user["id"],),
        fetchall=False,
    )
    if not row:
        raise HTTPException(status_code=404, detail="Usuário não encontrado")
    senha_hash = hash_password(body.senha_nova)
    query(
        "UPDATE usuarios SET senha_hash = %s, atualizado_em = NOW() WHERE id = %s",
        (senha_hash, current_user["id"]),
        fetchall=False,
    )
    return {"status": "ok"}


@app.post("/api/upload-xml")
async def upload_xml(files: list[UploadFile] = File(...), current_user: dict = Depends(get_current_user)):
    resultados = []
    # Obter lojas que este usuário gerencia
    lojas_permitidas = aplicar_filtro_segurança(current_user)
    
    with get_db() as conn:
        with conn.cursor() as cur:
            for f in files:
                try:
                    content = await f.read()
                    nfce = parse_nfce_xml(content, f.filename)
                    
                    # Verificar o CNPJ da loja no XML se não for Master
                    cnpj = nfce["loja"]["cnpj"]
                    cur.execute("SELECT id FROM lojas WHERE cnpj = %s", (cnpj,))
                    loja_row = cur.fetchone()
                    
                    if current_user["nivel_acesso"] != "master":
                        if not loja_row or loja_row[0] not in lojas_permitidas:
                            raise Exception("Você não tem permissão para importar notas para esta loja/CNPJ.")
                            
                    resultado = salvar_nfce(nfce)
                    resultado["arquivo"] = f.filename
                    resultados.append(resultado)
                except Exception as e:
                    resultados.append({"arquivo": f.filename, "status": "erro", "detalhe": str(e)})
    return resultados


@app.get("/api/dashboard")
def dashboard(
    loja_id: Optional[int] = None,
    data_inicio: Optional[str] = None,
    data_fim: Optional[str] = None,
    current_user: dict = Depends(get_current_user),
):
    """KPIs principais para o dashboard, filtráveis por loja e período"""
    lojas_permitidas = aplicar_filtro_segurança(current_user, loja_id)
    if not lojas_permitidas:
        return {
            "geral": {},
            "mes_atual": {},
            "alertas_recompra": 0,
            "clientes_inativos": 0,
            "filtro_periodo": bool(data_inicio or data_fim),
        }

    periodo_sql, periodo_params = clausula_periodo(data_inicio, data_fim)

    kpis_sql = f"""
        SELECT
            COUNT(DISTINCT id) AS total_notas,
            COUNT(DISTINCT cliente_id) AS total_clientes,
            ROUND(SUM(valor_total)::numeric, 2) AS faturamento_total,
            ROUND(SUM(valor_produtos)::numeric, 2) AS valor_bruto,
            ROUND(AVG(valor_total)::numeric, 2) AS ticket_medio,
            ROUND(SUM(valor_desconto)::numeric, 2) AS total_descontos,
            COUNT(*) FILTER (WHERE valor_desconto > 0) AS notas_com_desconto,
            ROUND(AVG(valor_desconto / NULLIF(valor_produtos, 0) * 100)
                FILTER (WHERE valor_desconto > 0 AND valor_produtos > 0)::numeric, 1) AS media_pct_desconto_notas
        FROM notas_fiscais
        WHERE loja_id = ANY(%s){periodo_sql}
    """
    kpis = query(kpis_sql, (lojas_permitidas, *periodo_params), fetchall=False)

    mes_atual = {}
    if not (data_inicio or data_fim):
        mes_sql = """
            SELECT
                COUNT(DISTINCT id) AS notas_mes,
                COUNT(DISTINCT cliente_id) AS clientes_mes,
                ROUND(SUM(valor_total)::numeric, 2) AS faturamento_mes,
                ROUND(SUM(valor_desconto)::numeric, 2) AS descontos_mes
            FROM notas_fiscais
            WHERE DATE_TRUNC('month', data_emissao) = DATE_TRUNC('month', NOW())
              AND loja_id = ANY(%s)
        """
        mes_atual = query(mes_sql, (lojas_permitidas,), fetchall=False)

    alertas_sql = """
        SELECT COUNT(*) AS total
        FROM vw_frequencia_recompra
        WHERE proxima_compra_estimada BETWEEN NOW() AND NOW() + INTERVAL '7 days'
          AND loja_id = ANY(%s)
    """
    alertas = query(alertas_sql, (lojas_permitidas,), fetchall=False)

    inativos_sql = "SELECT COUNT(*) AS total FROM vw_clientes_inativos WHERE loja_id = ANY(%s)"
    inativos = query(inativos_sql, (lojas_permitidas,), fetchall=False)

    top_desc_sql = f"""
        SELECT numero_nf, data_emissao, valor_produtos, valor_desconto, valor_total
        FROM notas_fiscais
        WHERE loja_id = ANY(%s) AND valor_desconto > 0{periodo_sql}
        ORDER BY valor_desconto DESC
        LIMIT 5
    """
    top_descontos = query(top_desc_sql, (lojas_permitidas, *periodo_params))

    return {
        "geral": dict(kpis) if kpis else {},
        "mes_atual": dict(mes_atual) if mes_atual else {},
        "alertas_recompra": dict(alertas)["total"] if alertas else 0,
        "clientes_inativos": dict(inativos)["total"] if inativos else 0,
        "filtro_periodo": bool(data_inicio or data_fim),
        "top_descontos": [dict(r) for r in top_descontos],
    }


@app.get("/api/faturamento-mensal")
def faturamento_mensal(loja_id: Optional[int] = None, current_user: dict = Depends(get_current_user)):
    """Faturamento mensal filtrável por lojas permitidas"""
    lojas_permitidas = aplicar_filtro_segurança(current_user, loja_id)
    if not lojas_permitidas:
        return []
        
    sql = """
        SELECT
            TO_CHAR(DATE_TRUNC('month', data_emissao), 'MM/YYYY') AS mes,
            DATE_TRUNC('month', data_emissao) AS mes_dt,
            ROUND(SUM(valor_total)::numeric, 2) AS faturamento,
            ROUND(SUM(valor_desconto)::numeric, 2) AS descontos,
            COUNT(DISTINCT id) AS notas,
            COUNT(DISTINCT cliente_id) AS clientes
        FROM notas_fiscais
        WHERE loja_id = ANY(%s)
        GROUP BY DATE_TRUNC('month', data_emissao)
        ORDER BY mes_dt
    """
    rows = query(sql, (lojas_permitidas,))
    return [dict(r) for r in rows]


ORDENACAO_PRODUTOS = {
    "unidades": "total_unidades DESC",
    "receita": "receita DESC",
    "clientes": "clientes_distintos DESC",
}


def filtros_produtos_vendas(
    q: str = "",
    categoria: str = "",
    data_inicio: Optional[str] = None,
    data_fim: Optional[str] = None,
) -> tuple:
    partes = []
    params = []
    periodo_sql, periodo_params = clausula_periodo(data_inicio, data_fim, "nf.data_emissao")
    if periodo_sql:
        partes.append(periodo_sql.lstrip(" AND "))
        params.extend(periodo_params)
    if categoria:
        partes.append("p.categoria = %s")
        params.append(categoria)
    if q:
        partes.append("LOWER(p.nome) LIKE LOWER(%s)")
        params.append(f"%{q}%")
    if not partes:
        return "", []
    return " AND " + " AND ".join(partes), params


def _sql_produtos_vendidos(ordenar: str) -> str:
    order_col = ORDENACAO_PRODUTOS.get(ordenar, ORDENACAO_PRODUTOS["unidades"])
    return f"""
        SELECT
            p.id, p.codigo, p.nome, p.categoria,
            SUM(iv.quantidade) AS total_unidades,
            ROUND(SUM(iv.valor_total)::numeric, 2) AS receita,
            ROUND(AVG(iv.valor_unitario)::numeric, 2) AS preco_medio,
            ROUND(AVG(NULLIF(iv.valor_desconto, 0))::numeric, 2) AS desconto_medio,
            COUNT(DISTINCT nf.cliente_id) AS clientes_distintos,
            false AS parado
        FROM itens_venda iv
        JOIN produtos p ON iv.produto_id = p.id
        JOIN notas_fiscais nf ON iv.nota_id = nf.id
        WHERE nf.loja_id = ANY(%s){{filtro_sql}}
        GROUP BY p.id, p.codigo, p.nome, p.categoria
        ORDER BY {order_col}
    """


@app.get("/api/produtos/categorias")
def categorias_produtos(loja_id: Optional[int] = None, current_user: dict = Depends(get_current_user)):
    lojas_permitidas = aplicar_filtro_segurança(current_user, loja_id)
    if not lojas_permitidas:
        return []
    rows = query("""
        SELECT DISTINCT categoria FROM produtos
        WHERE loja_id = ANY(%s) AND categoria IS NOT NULL AND categoria <> ''
        ORDER BY categoria
    """, (lojas_permitidas,))
    return [r["categoria"] for r in rows]


@app.get("/api/produtos/export")
def exportar_produtos_csv(
    loja_id: Optional[int] = None,
    data_inicio: Optional[str] = None,
    data_fim: Optional[str] = None,
    q: str = "",
    categoria: str = "",
    ordenar: str = "unidades",
    parados: bool = False,
    current_user: dict = Depends(get_current_user),
):
    lojas_permitidas = aplicar_filtro_segurança(current_user, loja_id)
    if not lojas_permitidas:
        raise HTTPException(404, "Nenhum produto encontrado")

    filtro_sql, filtro_params = filtros_produtos_vendas(q, categoria, data_inicio, data_fim)

    if parados:
        periodo_sql, periodo_params = clausula_periodo(data_inicio, data_fim, "nf.data_emissao")
        sql = f"""
            SELECT p.codigo, p.nome, p.categoria, p.preco_atual
            FROM produtos p
            WHERE p.loja_id = ANY(%s)
            {"AND LOWER(p.nome) LIKE LOWER(%s)" if q else ""}
            {"AND p.categoria = %s" if categoria else ""}
            AND NOT EXISTS (
                SELECT 1 FROM itens_venda iv
                JOIN notas_fiscais nf ON iv.nota_id = nf.id
                WHERE iv.produto_id = p.id AND nf.loja_id = ANY(%s){periodo_sql}
            )
            ORDER BY p.nome
        """
        params = [lojas_permitidas]
        if q:
            params.append(f"%{q}%")
        if categoria:
            params.append(categoria)
        params.extend([lojas_permitidas, *periodo_params])
        rows = query(sql, params)
        output = io.StringIO()
        writer = csv.writer(output, delimiter=";")
        writer.writerow(["Codigo", "Produto", "Categoria", "Preco Atual"])
        for r in rows:
            writer.writerow([r["codigo"], r["nome"], r["categoria"], r["preco_atual"]])
    else:
        sql = _sql_produtos_vendidos(ordenar).format(filtro_sql=filtro_sql)
        rows = query(sql, (lojas_permitidas, *filtro_params))
        output = io.StringIO()
        writer = csv.writer(output, delimiter=";")
        writer.writerow([
            "Codigo", "Produto", "Categoria", "Unidades",
            "Receita", "Preco Medio", "Desconto Medio", "Clientes",
        ])
        for r in rows:
            writer.writerow([
                r["codigo"], r["nome"], r["categoria"],
                r["total_unidades"], r["receita"], r["preco_medio"],
                r["desconto_medio"] or 0, r["clientes_distintos"],
            ])

    return Response(
        content="\ufeff" + output.getvalue(),
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": "attachment; filename=produtos.csv"},
    )


@app.get("/api/produtos/{produto_id}")
def detalhe_produto(
    produto_id: int,
    loja_id: Optional[int] = None,
    data_inicio: Optional[str] = None,
    data_fim: Optional[str] = None,
    current_user: dict = Depends(get_current_user),
):
    lojas_permitidas = aplicar_filtro_segurança(current_user, loja_id)
    if not lojas_permitidas:
        raise HTTPException(403, "Acesso negado")

    produto = query(
        "SELECT * FROM produtos WHERE id = %s AND loja_id = ANY(%s)",
        (produto_id, lojas_permitidas),
        fetchall=False,
    )
    if not produto:
        raise HTTPException(404, "Produto não encontrado")

    filtro_sql, filtro_params = filtros_produtos_vendas("", "", data_inicio, data_fim)

    resumo = query(f"""
        SELECT
            SUM(iv.quantidade) AS total_unidades,
            ROUND(SUM(iv.valor_total)::numeric, 2) AS receita,
            ROUND(AVG(iv.valor_unitario)::numeric, 2) AS preco_medio,
            ROUND(AVG(NULLIF(iv.valor_desconto, 0))::numeric, 2) AS desconto_medio,
            COUNT(DISTINCT nf.cliente_id) AS clientes_distintos,
            COUNT(DISTINCT nf.id) AS total_notas
        FROM itens_venda iv
        JOIN notas_fiscais nf ON iv.nota_id = nf.id
        WHERE iv.produto_id = %s AND nf.loja_id = ANY(%s){filtro_sql}
    """, (produto_id, lojas_permitidas, *filtro_params), fetchall=False)

    clientes = query(f"""
        SELECT COALESCE(c.nome, 'Consumidor Não Identificado') AS cliente,
            c.id AS cliente_id,
            SUM(iv.quantidade) AS total_unidades,
            ROUND(SUM(iv.valor_total)::numeric, 2) AS total_gasto,
            MAX(nf.data_emissao) AS ultima_compra
        FROM itens_venda iv
        JOIN notas_fiscais nf ON iv.nota_id = nf.id
        LEFT JOIN clientes c ON nf.cliente_id = c.id
        WHERE iv.produto_id = %s AND nf.loja_id = ANY(%s){filtro_sql}
        GROUP BY c.id, c.nome
        ORDER BY total_gasto DESC
        LIMIT 20
    """, (produto_id, lojas_permitidas, *filtro_params))

    vendas = query(f"""
        SELECT nf.numero_nf, nf.data_emissao,
            COALESCE(c.nome, 'Consumidor Não Identificado') AS cliente,
            iv.quantidade, iv.valor_unitario, iv.valor_desconto, iv.valor_total
        FROM itens_venda iv
        JOIN notas_fiscais nf ON iv.nota_id = nf.id
        LEFT JOIN clientes c ON nf.cliente_id = c.id
        WHERE iv.produto_id = %s AND nf.loja_id = ANY(%s){filtro_sql}
        ORDER BY nf.data_emissao DESC
        LIMIT 15
    """, (produto_id, lojas_permitidas, *filtro_params))

    return {
        "produto": dict(produto),
        "resumo": dict(resumo) if resumo else {},
        "clientes": [dict(r) for r in clientes],
        "vendas": [dict(r) for r in vendas],
    }


@app.get("/api/produtos")
def listar_produtos(
    loja_id: Optional[int] = None,
    data_inicio: Optional[str] = None,
    data_fim: Optional[str] = None,
    q: str = "",
    categoria: str = "",
    ordenar: str = "unidades",
    parados: bool = False,
    limit: int = 50,
    offset: int = 0,
    current_user: dict = Depends(get_current_user),
):
    """Lista produtos vendidos ou parados no período."""
    lojas_permitidas = aplicar_filtro_segurança(current_user, loja_id)
    if not lojas_permitidas:
        return {"produtos": [], "total": 0, "limit": limit, "offset": offset, "modo": "parados" if parados else "vendidos"}

    filtro_sql, filtro_params = filtros_produtos_vendas(q, categoria, data_inicio, data_fim)

    if parados:
        periodo_sql, periodo_params = clausula_periodo(data_inicio, data_fim, "nf.data_emissao")
        base_where = "p.loja_id = ANY(%s)"
        params = [lojas_permitidas]
        if q:
            base_where += " AND LOWER(p.nome) LIKE LOWER(%s)"
            params.append(f"%{q}%")
        if categoria:
            base_where += " AND p.categoria = %s"
            params.append(categoria)

        sql = f"""
            SELECT p.id, p.codigo, p.nome, p.categoria, p.preco_atual, true AS parado,
                NULL::numeric AS total_unidades, NULL::numeric AS receita,
                NULL::numeric AS preco_medio, NULL::numeric AS desconto_medio,
                0 AS clientes_distintos
            FROM produtos p
            WHERE {base_where}
            AND NOT EXISTS (
                SELECT 1 FROM itens_venda iv
                JOIN notas_fiscais nf ON iv.nota_id = nf.id
                WHERE iv.produto_id = p.id AND nf.loja_id = ANY(%s){periodo_sql}
            )
            ORDER BY p.nome
            LIMIT %s OFFSET %s
        """
        params.extend([lojas_permitidas, *periodo_params, limit, offset])
        rows = query(sql, params)

        count_sql = f"""
            SELECT COUNT(*) AS t FROM produtos p
            WHERE {base_where}
            AND NOT EXISTS (
                SELECT 1 FROM itens_venda iv
                JOIN notas_fiscais nf ON iv.nota_id = nf.id
                WHERE iv.produto_id = p.id AND nf.loja_id = ANY(%s){periodo_sql}
            )
        """
        count_params = [lojas_permitidas]
        if q:
            count_params.append(f"%{q}%")
        if categoria:
            count_params.append(categoria)
        count_params.extend([lojas_permitidas, *periodo_params])
        total = query(count_sql, count_params, fetchall=False)
    else:
        sql = _sql_produtos_vendidos(ordenar).format(filtro_sql=filtro_sql)
        sql += " LIMIT %s OFFSET %s"
        params = [lojas_permitidas, *filtro_params, limit, offset]
        rows = query(sql, params)

        count_sql = f"""
            SELECT COUNT(*) AS t FROM (
                SELECT p.id
                FROM itens_venda iv
                JOIN produtos p ON iv.produto_id = p.id
                JOIN notas_fiscais nf ON iv.nota_id = nf.id
                WHERE nf.loja_id = ANY(%s){filtro_sql}
                GROUP BY p.id
            ) sub
        """
        total = query(count_sql, (lojas_permitidas, *filtro_params), fetchall=False)

    return {
        "produtos": [dict(r) for r in rows],
        "total": dict(total)["t"] if total else 0,
        "limit": limit,
        "offset": offset,
        "modo": "parados" if parados else "vendidos",
    }


@app.get("/api/produtos-mais-vendidos")
def produtos_mais_vendidos(
    limit: int = 10,
    loja_id: Optional[int] = None,
    data_inicio: Optional[str] = None,
    data_fim: Optional[str] = None,
    current_user: dict = Depends(get_current_user),
):
    """Produtos mais vendidos, filtráveis por loja e período"""
    lojas_permitidas = aplicar_filtro_segurança(current_user, loja_id)
    if not lojas_permitidas:
        return []

    periodo_sql, periodo_params = clausula_periodo(data_inicio, data_fim, "nf.data_emissao")
    sql = f"""
        SELECT
            p.nome,
            p.categoria,
            SUM(iv.quantidade) AS total_unidades,
            ROUND(SUM(iv.valor_total)::numeric, 2) AS receita,
            COUNT(DISTINCT nf.cliente_id) AS clientes_distintos
        FROM itens_venda iv
        JOIN produtos p ON iv.produto_id = p.id
        JOIN notas_fiscais nf ON iv.nota_id = nf.id
        WHERE nf.loja_id = ANY(%s){periodo_sql}
        GROUP BY p.id, p.nome, p.categoria
        ORDER BY total_unidades DESC
        LIMIT %s
    """
    rows = query(sql, (lojas_permitidas, *periodo_params, limit))
    return [dict(r) for r in rows]


@app.get("/api/alertas-recompra")
def alertas_recompra(
    dias: int = 7,
    dias_atraso: int = 3,
    loja_id: Optional[int] = None,
    q: str = "",
    com_telefone: bool = False,
    apenas_atrasados: bool = False,
    current_user: dict = Depends(get_current_user),
):
    """Alertas de recompra por par cliente+produto, filtráveis por janela e busca."""
    lojas_permitidas = aplicar_filtro_segurança(current_user, loja_id)
    if not lojas_permitidas:
        return {"alertas": [], "total": 0, "dias": dias, "dias_atraso": dias_atraso}

    dias_atraso = max(1, min(dias_atraso, 365))

    partes = [
        "proxima_compra_estimada BETWEEN NOW() - (INTERVAL '1 day' * %s) AND NOW() + (INTERVAL '1 day' * %s)",
        "loja_id = ANY(%s)",
    ]
    params: list = [dias_atraso, dias, lojas_permitidas]

    if q:
        partes.append("(LOWER(cliente) LIKE LOWER(%s) OR LOWER(produto) LIKE LOWER(%s))")
        params.extend([f"%{q}%", f"%{q}%"])
    if com_telefone:
        partes.append("telefone IS NOT NULL AND TRIM(telefone) <> ''")
    if apenas_atrasados:
        partes.append("proxima_compra_estimada < NOW()")

    where_sql = " AND ".join(partes)
    sql = f"""
        SELECT
            cliente_id,
            cliente,
            telefone,
            produto_id,
            produto,
            total_compras,
            ultima_compra,
            intervalo_medio_dias,
            proxima_compra_estimada,
            ROUND(EXTRACT(DAY FROM proxima_compra_estimada - NOW())) AS dias_restantes
        FROM vw_frequencia_recompra
        WHERE {where_sql}
        ORDER BY proxima_compra_estimada
    """
    rows = query(sql, params)
    alertas = [dict(r) for r in rows]
    return {"alertas": alertas, "total": len(alertas), "dias": dias, "dias_atraso": dias_atraso}


@app.get("/api/clientes-inativos")
def clientes_inativos(
    loja_id: Optional[int] = None,
    q: str = "",
    com_telefone: bool = False,
    min_dias: int = 60,
    min_gasto: Optional[float] = None,
    min_compras: int = 0,
    limit: int = 50,
    offset: int = 0,
    current_user: dict = Depends(get_current_user),
):
    """Clientes sem compra há N+ dias, com produto favorito para abordagem comercial."""
    lojas_permitidas = aplicar_filtro_segurança(current_user, loja_id)
    if not lojas_permitidas:
        return {"clientes": [], "total": 0, "limit": limit, "offset": offset, "min_dias": min_dias}

    min_dias = max(30, min(min_dias, 730))

    filtro_partes = []
    filtro_params: list = []
    if q:
        filtro_partes.append("(LOWER(c.nome) LIKE LOWER(%s) OR c.telefone LIKE %s)")
        like = f"%{q}%"
        filtro_params.extend([like, like])
    if com_telefone:
        filtro_partes.append("c.telefone IS NOT NULL AND TRIM(c.telefone) <> ''")
    filtro_sql = (" AND " + " AND ".join(filtro_partes)) if filtro_partes else ""

    having_partes = []
    having_params: list = []
    if min_gasto is not None and min_gasto > 0:
        having_partes.append("SUM(nf.valor_total) >= %s")
        having_params.append(min_gasto)
    if min_compras > 0:
        having_partes.append("COUNT(DISTINCT nf.id) >= %s")
        having_params.append(min_compras)
    having_extra = (" AND " + " AND ".join(having_partes)) if having_partes else ""

    base_sql = f"""
        SELECT
            c.id,
            c.nome,
            c.telefone,
            nf.loja_id,
            MAX(nf.data_emissao) AS ultima_compra,
            EXTRACT(DAY FROM NOW() - MAX(nf.data_emissao))::int AS dias_sem_comprar,
            COUNT(DISTINCT nf.id) AS total_notas,
            ROUND(SUM(nf.valor_total)::numeric, 2) AS total_gasto,
            (
                SELECT p.nome
                FROM notas_fiscais nf2
                JOIN itens_venda iv ON iv.nota_id = nf2.id
                JOIN produtos p ON iv.produto_id = p.id
                WHERE nf2.cliente_id = c.id AND nf2.loja_id = nf.loja_id
                ORDER BY nf2.data_emissao DESC, iv.valor_total DESC
                LIMIT 1
            ) AS produto_ultimo,
            (
                SELECT p.nome
                FROM itens_venda iv
                JOIN notas_fiscais nf2 ON iv.nota_id = nf2.id
                JOIN produtos p ON iv.produto_id = p.id
                WHERE nf2.cliente_id = c.id AND nf2.loja_id = nf.loja_id
                GROUP BY p.id, p.nome
                ORDER BY SUM(iv.valor_total) DESC
                LIMIT 1
            ) AS produto_favorito
        FROM clientes c
        JOIN notas_fiscais nf ON c.id = nf.cliente_id
        WHERE nf.loja_id = ANY(%s){filtro_sql}
        GROUP BY c.id, c.nome, c.telefone, nf.loja_id
        HAVING MAX(nf.data_emissao) < NOW() - (INTERVAL '1 day' * %s){having_extra}
    """

    sql = base_sql + " ORDER BY dias_sem_comprar DESC LIMIT %s OFFSET %s"
    params = [lojas_permitidas, *filtro_params, min_dias, *having_params, limit, offset]
    rows = query(sql, params)

    count_sql = f"SELECT COUNT(*) AS t FROM ({base_sql}) sub"
    count_params = [lojas_permitidas, *filtro_params, min_dias, *having_params]
    total = query(count_sql, count_params, fetchall=False)

    return {
        "clientes": [dict(r) for r in rows],
        "total": dict(total)["t"] if total else 0,
        "limit": limit,
        "offset": offset,
        "min_dias": min_dias,
    }


@app.get("/api/clientes")
def listar_clientes(
    q: str = "",
    loja_id: Optional[int] = None,
    data_inicio: Optional[str] = None,
    data_fim: Optional[str] = None,
    min_compras: int = 0,
    min_gasto: Optional[float] = None,
    com_telefone: bool = False,
    limit: int = 50,
    offset: int = 0,
    current_user: dict = Depends(get_current_user),
):
    """Lista clientes com consolidado de compras e filtros."""
    lojas_permitidas = aplicar_filtro_segurança(current_user, loja_id)
    if not lojas_permitidas:
        return {"clientes": [], "total": 0, "limit": limit, "offset": offset}

    having_parts = []
    having_params = []

    periodo_sql, periodo_params = clausula_periodo(data_inicio, data_fim, "nf.data_emissao")
    if min_compras > 0:
        having_parts.append("COUNT(DISTINCT nf.id) >= %s")
        having_params.append(min_compras)
    if min_gasto is not None and min_gasto > 0:
        having_parts.append("SUM(nf.valor_total) >= %s")
        having_params.append(min_gasto)

    having_sql = (" HAVING " + " AND ".join(having_parts)) if having_parts else ""

    sql = f"""
        SELECT c.id, c.nome, c.telefone, c.municipio, c.uf,
            COUNT(DISTINCT nf.id) AS total_compras,
            ROUND(SUM(nf.valor_total)::numeric, 2) AS total_gasto,
            MAX(nf.data_emissao) AS ultima_compra
        FROM clientes c
        JOIN notas_fiscais nf ON c.id = nf.cliente_id
        WHERE nf.loja_id = ANY(%s){periodo_sql}
    """
    params = [lojas_permitidas, *periodo_params]

    if q:
        sql += " AND (LOWER(c.nome) LIKE LOWER(%s) OR c.telefone LIKE %s)"
        like = f"%{q}%"
        params.extend([like, like])
    if com_telefone:
        sql += " AND c.telefone IS NOT NULL AND c.telefone <> ''"

    sql += f" GROUP BY c.id, c.nome, c.telefone, c.municipio, c.uf{having_sql}"
    sql += " ORDER BY total_gasto DESC NULLS LAST LIMIT %s OFFSET %s"
    params.extend(having_params)
    params.extend([limit, offset])

    rows = query(sql, params)

    count_sql = f"""
        SELECT COUNT(*) AS t FROM (
            SELECT c.id
            FROM clientes c
            JOIN notas_fiscais nf ON c.id = nf.cliente_id
            WHERE nf.loja_id = ANY(%s){periodo_sql}
    """
    count_params = [lojas_permitidas, *periodo_params]
    if q:
        count_sql += " AND (LOWER(c.nome) LIKE LOWER(%s) OR c.telefone LIKE %s)"
        like = f"%{q}%"
        count_params.extend([like, like])
    if com_telefone:
        count_sql += " AND c.telefone IS NOT NULL AND c.telefone <> ''"
    count_sql += f" GROUP BY c.id{having_sql}) sub"
    count_params.extend(having_params)
    total = query(count_sql, count_params, fetchall=False)

    return {
        "clientes": [dict(r) for r in rows],
        "total": dict(total)["t"] if total else 0,
        "limit": limit,
        "offset": offset,
    }


@app.put("/api/clientes/{cliente_id}")
def atualizar_cliente(
    cliente_id: int,
    req: ClienteUpdate,
    current_user: dict = Depends(get_current_user)
):
    """Atualiza as informações de contato do cliente (nome e telefone)."""
    row = query("SELECT id FROM clientes WHERE id = %s", (cliente_id,), fetchall=False)
    if not row:
        raise HTTPException(status_code=404, detail="Cliente não encontrado")
    
    execute("""
        UPDATE clientes 
        SET nome = %s, telefone = %s, atualizado_em = NOW() 
        WHERE id = %s
    """, (req.nome, req.telefone, cliente_id))
    
    return {"status": "ok", "msg": "Cliente atualizado com sucesso"}


@app.get("/api/clientes/{cliente_id}/historico")
def historico_cliente(cliente_id: int, loja_id: Optional[int] = None, current_user: dict = Depends(get_current_user)):
    """Histórico individual do cliente filtrável opcionalmente por lojas permitidas"""
    lojas_permitidas = aplicar_filtro_segurança(current_user, loja_id)
    if not lojas_permitidas:
        raise HTTPException(403, "Acesso negado para esta loja.")
        
    cliente = query("SELECT * FROM clientes WHERE id = %s", (cliente_id,), fetchall=False)
    if not cliente:
        raise HTTPException(404, "Cliente não encontrado")

    notas_sql = """
        SELECT nf.numero_nf, nf.data_emissao, nf.valor_total, nf.forma_pagamento 
        FROM notas_fiscais nf 
        WHERE nf.cliente_id = %s AND nf.loja_id = ANY(%s)
        ORDER BY nf.data_emissao DESC
    """
    notas = query(notas_sql, (cliente_id, lojas_permitidas))

    produtos_sql = """
        SELECT p.nome, p.categoria,
            SUM(iv.quantidade) AS total_unidades,
            ROUND(SUM(iv.valor_total)::numeric, 2) AS total_gasto,
            MAX(nf.data_emissao) AS ultima_compra
        FROM itens_venda iv
        JOIN produtos p ON iv.produto_id = p.id
        JOIN notas_fiscais nf ON iv.nota_id = nf.id
        WHERE nf.cliente_id = %s AND nf.loja_id = ANY(%s)
        GROUP BY p.id, p.nome, p.categoria ORDER BY total_gasto DESC
    """
    produtos_comprados = query(produtos_sql, (cliente_id, lojas_permitidas))

    recompras_sql = """
        SELECT produto, intervalo_medio_dias, ultima_compra, proxima_compra_estimada,
            ROUND(EXTRACT(DAY FROM proxima_compra_estimada - NOW())) AS dias_restantes
        FROM vw_frequencia_recompra
        WHERE cliente_id = %s AND loja_id = ANY(%s)
        ORDER BY proxima_compra_estimada
    """
    recompras = query(recompras_sql, (cliente_id, lojas_permitidas))

    cliente_pub = dict(cliente)
    cliente_pub.pop("cpf", None)

    return {
        "cliente": cliente_pub,
        "notas": [dict(r) for r in notas],
        "produtos": [dict(r) for r in produtos_comprados],
        "recompras_previstas": [dict(r) for r in recompras],
    }


@app.get("/api/vendas-por-categoria")
def vendas_categoria(
    loja_id: Optional[int] = None,
    data_inicio: Optional[str] = None,
    data_fim: Optional[str] = None,
    current_user: dict = Depends(get_current_user),
):
    """Gráfico de pizza por categoria, filtrável por loja e período"""
    lojas_permitidas = aplicar_filtro_segurança(current_user, loja_id)
    if not lojas_permitidas:
        return []

    periodo_sql, periodo_params = clausula_periodo(data_inicio, data_fim, "nf.data_emissao")
    sql = f"""
        SELECT p.categoria,
            ROUND(SUM(iv.valor_total)::numeric, 2) AS receita,
            SUM(iv.quantidade) AS unidades
        FROM itens_venda iv
        JOIN produtos p ON iv.produto_id = p.id
        JOIN notas_fiscais nf ON iv.nota_id = nf.id
        WHERE nf.loja_id = ANY(%s){periodo_sql}
        GROUP BY p.categoria ORDER BY receita DESC
    """
    rows = query(sql, (lojas_permitidas, *periodo_params))
    return [dict(r) for r in rows]


@app.get("/api/vendas-por-pagamento")
def vendas_pagamento(
    loja_id: Optional[int] = None,
    data_inicio: Optional[str] = None,
    data_fim: Optional[str] = None,
    current_user: dict = Depends(get_current_user),
):
    """Distribuição de vendas por forma de pagamento"""
    lojas_permitidas = aplicar_filtro_segurança(current_user, loja_id)
    if not lojas_permitidas:
        return []

    periodo_sql, periodo_params = clausula_periodo(data_inicio, data_fim)
    sql = f"""
        SELECT
            COALESCE(NULLIF(forma_pagamento, ''), 'Não informado') AS forma_pagamento,
            COUNT(*) AS notas,
            ROUND(SUM(valor_total)::numeric, 2) AS receita
        FROM notas_fiscais
        WHERE loja_id = ANY(%s){periodo_sql}
        GROUP BY forma_pagamento
        ORDER BY receita DESC
    """
    rows = query(sql, (lojas_permitidas, *periodo_params))
    return [dict(r) for r in rows]


def filtros_notas_sql(
    q: str = "",
    forma_pagamento: str = "",
    data_inicio: Optional[str] = None,
    data_fim: Optional[str] = None,
) -> tuple:
    """Retorna fragmento SQL (AND ...) e parâmetros para filtros de notas."""
    partes = []
    params = []
    periodo_sql, periodo_params = clausula_periodo(data_inicio, data_fim, "nf.data_emissao")
    if periodo_sql:
        partes.append(periodo_sql.lstrip(" AND "))
        params.extend(periodo_params)
    if forma_pagamento:
        partes.append("nf.forma_pagamento = %s")
        params.append(forma_pagamento)
    if q:
        partes.append(
            "(nf.numero_nf ILIKE %s OR LOWER(COALESCE(c.nome, '')) LIKE LOWER(%s) OR COALESCE(c.cpf, '') LIKE %s)"
        )
        like = f"%{q}%"
        params.extend([like, like, like])
    if not partes:
        return "", []
    return " AND " + " AND ".join(partes), params


@app.get("/api/notas/formas-pagamento")
def formas_pagamento_notas(loja_id: Optional[int] = None, current_user: dict = Depends(get_current_user)):
    """Lista formas de pagamento distintas para filtro"""
    lojas_permitidas = aplicar_filtro_segurança(current_user, loja_id)
    if not lojas_permitidas:
        return []
    rows = query("""
        SELECT DISTINCT forma_pagamento
        FROM notas_fiscais
        WHERE loja_id = ANY(%s) AND forma_pagamento IS NOT NULL AND forma_pagamento <> ''
        ORDER BY forma_pagamento
    """, (lojas_permitidas,))
    return [r["forma_pagamento"] for r in rows]


@app.get("/api/notas/export")
def exportar_notas_csv(
    loja_id: Optional[int] = None,
    data_inicio: Optional[str] = None,
    data_fim: Optional[str] = None,
    q: str = "",
    forma_pagamento: str = "",
    current_user: dict = Depends(get_current_user),
):
    """Exporta notas filtradas em CSV"""
    lojas_permitidas = aplicar_filtro_segurança(current_user, loja_id)
    if not lojas_permitidas:
        raise HTTPException(404, "Nenhuma nota encontrada")

    filtro_sql, filtro_params = filtros_notas_sql(q, forma_pagamento, data_inicio, data_fim)
    sql = f"""
        SELECT nf.numero_nf, nf.data_emissao, l.nome_fantasia AS loja,
            COALESCE(c.nome, 'Consumidor Não Identificado') AS cliente,
            c.cpf, nf.valor_produtos, nf.valor_desconto, nf.valor_total,
            nf.forma_pagamento, nf.chave_nfe
        FROM notas_fiscais nf
        LEFT JOIN clientes c ON nf.cliente_id = c.id
        JOIN lojas l ON nf.loja_id = l.id
        WHERE nf.loja_id = ANY(%s){filtro_sql}
        ORDER BY nf.data_emissao DESC
    """
    rows = query(sql, (lojas_permitidas, *filtro_params))

    output = io.StringIO()
    writer = csv.writer(output, delimiter=";")
    writer.writerow([
        "Numero NF", "Data Emissao", "Loja", "Cliente", "CPF",
        "Valor Bruto", "Desconto", "Valor Total", "Forma Pagamento", "Chave NFe",
    ])
    for r in rows:
        writer.writerow([
            r["numero_nf"],
            r["data_emissao"].strftime("%d/%m/%Y %H:%M") if r["data_emissao"] else "",
            r["loja"],
            r["cliente"],
            r["cpf"] or "",
            r["valor_produtos"],
            r["valor_desconto"],
            r["valor_total"],
            r["forma_pagamento"],
            r["chave_nfe"],
        ])

    return Response(
        content="\ufeff" + output.getvalue(),
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": "attachment; filename=notas_fiscais.csv"},
    )


@app.get("/api/notas/{nota_id}")
def detalhe_nota(nota_id: int, current_user: dict = Depends(get_current_user)):
    """Detalhe de uma nota fiscal com itens"""
    lojas_permitidas = aplicar_filtro_segurança(current_user)
    if not lojas_permitidas:
        raise HTTPException(403, "Acesso negado")

    nota = query("""
        SELECT nf.id, nf.numero_nf, nf.serie, nf.chave_nfe, nf.data_emissao,
            nf.valor_produtos, nf.valor_desconto, nf.valor_total, nf.forma_pagamento,
            nf.xml_filename, nf.cliente_id,
            COALESCE(c.nome, 'Consumidor Não Identificado') AS cliente,
            c.cpf, c.telefone,
            l.nome_fantasia AS loja
        FROM notas_fiscais nf
        LEFT JOIN clientes c ON nf.cliente_id = c.id
        JOIN lojas l ON nf.loja_id = l.id
        WHERE nf.id = %s AND nf.loja_id = ANY(%s)
    """, (nota_id, lojas_permitidas), fetchall=False)

    if not nota:
        raise HTTPException(404, "Nota não encontrada")

    itens = query("""
        SELECT p.nome, p.categoria, iv.quantidade, iv.valor_unitario,
            iv.valor_desconto, iv.valor_total
        FROM itens_venda iv
        JOIN produtos p ON iv.produto_id = p.id
        WHERE iv.nota_id = %s
        ORDER BY iv.valor_total DESC
    """, (nota_id,))

    return {"nota": dict(nota), "itens": [dict(i) for i in itens]}


@app.get("/api/notas")
def listar_notas(
    limit: int = 25,
    offset: int = 0,
    loja_id: Optional[int] = None,
    data_inicio: Optional[str] = None,
    data_fim: Optional[str] = None,
    q: str = "",
    forma_pagamento: str = "",
    current_user: dict = Depends(get_current_user),
):
    """Lista notas fiscais importadas com filtros e paginação"""
    lojas_permitidas = aplicar_filtro_segurança(current_user, loja_id)
    if not lojas_permitidas:
        return {"notas": [], "total": 0, "limit": limit, "offset": offset}

    filtro_sql, filtro_params = filtros_notas_sql(q, forma_pagamento, data_inicio, data_fim)
    sql = f"""
        SELECT nf.id, nf.numero_nf, nf.data_emissao, nf.cliente_id,
            COALESCE(c.nome, 'Consumidor Não Identificado') AS cliente,
            nf.valor_produtos, nf.valor_desconto, nf.valor_total,
            nf.forma_pagamento, nf.xml_filename, l.nome_fantasia AS loja
        FROM notas_fiscais nf
        LEFT JOIN clientes c ON nf.cliente_id = c.id
        JOIN lojas l ON nf.loja_id = l.id
        WHERE nf.loja_id = ANY(%s){filtro_sql}
        ORDER BY nf.data_emissao DESC LIMIT %s OFFSET %s
    """
    rows = query(sql, (lojas_permitidas, *filtro_params, limit, offset))

    count_sql = f"""
        SELECT COUNT(*) AS t
        FROM notas_fiscais nf
        LEFT JOIN clientes c ON nf.cliente_id = c.id
        WHERE nf.loja_id = ANY(%s){filtro_sql}
    """
    total = query(count_sql, (lojas_permitidas, *filtro_params), fetchall=False)

    return {
        "notas": [dict(r) for r in rows],
        "total": dict(total)["t"],
        "limit": limit,
        "offset": offset,
    }


def _params_analises(loja_id, data_inicio, data_fim, current_user):
    lojas_permitidas = aplicar_filtro_segurança(current_user, loja_id)
    periodo_sql, periodo_params = clausula_periodo(data_inicio, data_fim, "nf.data_emissao")
    periodo_nf_sql, periodo_nf_params = clausula_periodo(data_inicio, data_fim, "data_emissao")
    multi_loja = len(lojas_permitidas) > 1
    return lojas_permitidas, periodo_sql, periodo_params, periodo_nf_sql, periodo_nf_params, multi_loja


def _comportamento_vazio():
    return {
        "clientes": {
            "novos": {"notas": 0, "clientes": 0, "faturamento": 0, "ticket_medio": 0, "pct_notas": 0, "pct_faturamento": 0},
            "recorrentes": {"notas": 0, "clientes": 0, "faturamento": 0, "ticket_medio": 0, "pct_notas": 0, "pct_faturamento": 0},
            "sem_identificacao": {"notas": 0, "faturamento": 0, "ticket_medio": 0, "pct_notas": 0, "pct_faturamento": 0},
            "total_notas": 0,
        },
        "ticket_pagamento": [],
        "ticket_categoria": [],
        "ticket_loja": [],
    }


def _descontos_analise_vazio():
    return {
        "resumo": {"total_desconto": 0, "valor_bruto": 0, "pct_bruto": 0, "notas_com_desconto": 0, "pct_notas": 0},
        "por_categoria": [],
        "por_produto": [],
    }


def _montar_clientes_novos_rec(rows, sem_id_row):
    base = {
        "novos": {"notas": 0, "clientes": 0, "faturamento": 0.0, "ticket_medio": 0.0, "pct_notas": 0.0, "pct_faturamento": 0.0},
        "recorrentes": {"notas": 0, "clientes": 0, "faturamento": 0.0, "ticket_medio": 0.0, "pct_notas": 0.0, "pct_faturamento": 0.0},
        "sem_identificacao": {"notas": 0, "faturamento": 0.0, "ticket_medio": 0.0, "pct_notas": 0.0, "pct_faturamento": 0.0},
        "total_notas": 0,
    }
    for r in rows:
        key = r["tipo"]
        base[key] = {
            "notas": int(r["notas"] or 0),
            "clientes": int(r["clientes"] or 0),
            "faturamento": float(r["faturamento"] or 0),
            "ticket_medio": float(r["ticket_medio"] or 0),
            "pct_notas": 0.0,
            "pct_faturamento": 0.0,
        }
    if sem_id_row:
        base["sem_identificacao"] = {
            "notas": int(sem_id_row["notas"] or 0),
            "faturamento": float(sem_id_row["faturamento"] or 0),
            "ticket_medio": float(sem_id_row["ticket_medio"] or 0),
            "pct_notas": 0.0,
            "pct_faturamento": 0.0,
        }
    total_notas = base["novos"]["notas"] + base["recorrentes"]["notas"] + base["sem_identificacao"]["notas"]
    total_fat = base["novos"]["faturamento"] + base["recorrentes"]["faturamento"] + base["sem_identificacao"]["faturamento"]
    base["total_notas"] = total_notas
    for bloco in ("novos", "recorrentes", "sem_identificacao"):
        if total_notas:
            base[bloco]["pct_notas"] = round(100.0 * base[bloco]["notas"] / total_notas, 1)
        if total_fat:
            base[bloco]["pct_faturamento"] = round(100.0 * base[bloco]["faturamento"] / total_fat, 1)
    return base


@app.get("/api/analises")
def analises_fase1(
    loja_id: Optional[int] = None,
    data_inicio: Optional[str] = None,
    data_fim: Optional[str] = None,
    current_user: dict = Depends(get_current_user),
):
    """Análises: sazonalidade, top lojas, produtos por loja e comportamento (Fase 3)."""
    lojas_permitidas, periodo_sql, periodo_params, periodo_nf_sql, periodo_nf_params, multi_loja = _params_analises(
        loja_id, data_inicio, data_fim, current_user
    )
    if not lojas_permitidas:
        return {
            "multi_loja": False,
            "total_lojas": 0,
            "sazonalidade": {"dia_semana": [], "dia_mes": [], "hora": [], "picos": {}},
            "top_lojas": [],
            "top_produtos_por_loja": [],
            "comportamento": _comportamento_vazio(),
            "descontos_analise": _descontos_analise_vazio(),
            "vendas_por_cidade": [],
        }

    dia_semana = query(f"""
        SELECT
            CASE EXTRACT(DOW FROM data_emissao)::int WHEN 0 THEN 7
                ELSE EXTRACT(DOW FROM data_emissao)::int END AS ordem,
            CASE EXTRACT(DOW FROM data_emissao)::int
                WHEN 0 THEN 'Dom' WHEN 1 THEN 'Seg' WHEN 2 THEN 'Ter' WHEN 3 THEN 'Qua'
                WHEN 4 THEN 'Qui' WHEN 5 THEN 'Sex' ELSE 'Sáb' END AS label,
            COUNT(*) AS notas,
            ROUND(SUM(valor_total)::numeric, 2) AS faturamento
        FROM notas_fiscais
        WHERE loja_id = ANY(%s){periodo_nf_sql}
        GROUP BY EXTRACT(DOW FROM data_emissao)
        ORDER BY ordem
    """, (lojas_permitidas, *periodo_nf_params))

    dia_mes = query(f"""
        SELECT
            EXTRACT(DAY FROM data_emissao)::int AS dia,
            COUNT(*) AS notas,
            ROUND(SUM(valor_total)::numeric, 2) AS faturamento
        FROM notas_fiscais
        WHERE loja_id = ANY(%s){periodo_nf_sql}
        GROUP BY EXTRACT(DAY FROM data_emissao)
        ORDER BY dia
    """, (lojas_permitidas, *periodo_nf_params))

    hora = query(f"""
        SELECT
            EXTRACT(HOUR FROM data_emissao)::int AS hora,
            COUNT(*) AS notas,
            ROUND(SUM(valor_total)::numeric, 2) AS faturamento
        FROM notas_fiscais
        WHERE loja_id = ANY(%s){periodo_nf_sql}
        GROUP BY EXTRACT(HOUR FROM data_emissao)
        ORDER BY hora
    """, (lojas_permitidas, *periodo_nf_params))

    picos = {}
    if dia_semana:
        p = max(dia_semana, key=lambda r: float(r["faturamento"] or 0))
        picos["dia_semana"] = {"label": p["label"], "faturamento": float(p["faturamento"] or 0)}
    if dia_mes:
        p = max(dia_mes, key=lambda r: float(r["faturamento"] or 0))
        picos["dia_mes"] = {"dia": int(p["dia"]), "faturamento": float(p["faturamento"] or 0)}
    if hora:
        p = max(hora, key=lambda r: float(r["faturamento"] or 0))
        picos["hora"] = {"hora": int(p["hora"]), "faturamento": float(p["faturamento"] or 0)}

    top_lojas = []
    if multi_loja:
        top_lojas = query(f"""
            SELECT
                l.id AS loja_id,
                l.nome_fantasia AS loja,
                COALESCE(f.nome, '—') AS franquia,
                COUNT(DISTINCT nf.id) AS notas,
                ROUND(SUM(nf.valor_total)::numeric, 2) AS faturamento,
                ROUND(AVG(nf.valor_total)::numeric, 2) AS ticket_medio,
                COUNT(DISTINCT nf.cliente_id) AS clientes
            FROM notas_fiscais nf
            JOIN lojas l ON nf.loja_id = l.id
            LEFT JOIN franquias f ON l.franquia_id = f.id
            WHERE nf.loja_id = ANY(%s){periodo_nf_sql}
            GROUP BY l.id, l.nome_fantasia, f.nome
            ORDER BY faturamento DESC
            LIMIT 20
        """, (lojas_permitidas, *periodo_nf_params))

    top_produtos_por_loja = query(f"""
        WITH ranked AS (
            SELECT
                l.id AS loja_id,
                l.nome_fantasia AS loja,
                p.nome AS produto,
                p.categoria,
                SUM(iv.quantidade) AS unidades,
                ROUND(SUM(iv.valor_total)::numeric, 2) AS receita,
                ROW_NUMBER() OVER (
                    PARTITION BY l.id ORDER BY SUM(iv.valor_total) DESC
                ) AS posicao
            FROM itens_venda iv
            JOIN notas_fiscais nf ON iv.nota_id = nf.id
            JOIN produtos p ON iv.produto_id = p.id
            JOIN lojas l ON nf.loja_id = l.id
            WHERE nf.loja_id = ANY(%s){periodo_sql}
            GROUP BY l.id, l.nome_fantasia, p.id, p.nome, p.categoria
        )
        SELECT loja_id, loja, produto, categoria, unidades, receita, posicao
        FROM ranked
        WHERE posicao <= 5
        ORDER BY loja, posicao
    """, (lojas_permitidas, *periodo_params))

    clientes_tipos = query(f"""
        WITH notas_escopo AS (
            SELECT
                nf.id,
                nf.cliente_id,
                nf.valor_total,
                nf.data_emissao,
                ROW_NUMBER() OVER (
                    PARTITION BY nf.cliente_id
                    ORDER BY nf.data_emissao, nf.id
                ) AS ordem_compra
            FROM notas_fiscais nf
            WHERE nf.loja_id = ANY(%s) AND nf.cliente_id IS NOT NULL
        )
        SELECT
            CASE WHEN ordem_compra = 1 THEN 'novos' ELSE 'recorrentes' END AS tipo,
            COUNT(*) AS notas,
            COUNT(DISTINCT cliente_id) AS clientes,
            ROUND(SUM(valor_total)::numeric, 2) AS faturamento,
            ROUND(AVG(valor_total)::numeric, 2) AS ticket_medio
        FROM notas_escopo
        WHERE 1=1{periodo_nf_sql}
        GROUP BY 1
    """, (lojas_permitidas, *periodo_nf_params))

    sem_identificacao = query(f"""
        SELECT
            COUNT(*) AS notas,
            ROUND(SUM(valor_total)::numeric, 2) AS faturamento,
            ROUND(AVG(valor_total)::numeric, 2) AS ticket_medio
        FROM notas_fiscais nf
        WHERE nf.loja_id = ANY(%s){periodo_nf_sql} AND nf.cliente_id IS NULL
    """, (lojas_permitidas, *periodo_nf_params), fetchall=False)

    ticket_pagamento = query(f"""
        SELECT
            COALESCE(NULLIF(TRIM(forma_pagamento), ''), 'Não informado') AS pagamento,
            COUNT(*) AS notas,
            ROUND(SUM(valor_total)::numeric, 2) AS faturamento,
            ROUND(AVG(valor_total)::numeric, 2) AS ticket_medio
        FROM notas_fiscais nf
        WHERE nf.loja_id = ANY(%s){periodo_nf_sql}
        GROUP BY 1
        ORDER BY faturamento DESC
        LIMIT 12
    """, (lojas_permitidas, *periodo_nf_params))

    ticket_categoria = query(f"""
        SELECT
            COALESCE(NULLIF(TRIM(p.categoria), ''), 'Sem categoria') AS categoria,
            COUNT(DISTINCT nf.id) AS notas,
            ROUND(SUM(iv.valor_total)::numeric, 2) AS receita,
            ROUND(SUM(iv.valor_total)::numeric / NULLIF(COUNT(DISTINCT nf.id), 0), 2) AS ticket_medio
        FROM itens_venda iv
        JOIN notas_fiscais nf ON iv.nota_id = nf.id
        JOIN produtos p ON iv.produto_id = p.id
        WHERE nf.loja_id = ANY(%s){periodo_sql}
        GROUP BY 1
        HAVING COUNT(DISTINCT nf.id) >= 3
        ORDER BY receita DESC
        LIMIT 15
    """, (lojas_permitidas, *periodo_params))

    ticket_loja = []
    if multi_loja:
        ticket_loja = query(f"""
            SELECT
                l.nome_fantasia AS loja,
                COUNT(*) AS notas,
                ROUND(SUM(nf.valor_total)::numeric, 2) AS faturamento,
                ROUND(AVG(nf.valor_total)::numeric, 2) AS ticket_medio
            FROM notas_fiscais nf
            JOIN lojas l ON nf.loja_id = l.id
            WHERE nf.loja_id = ANY(%s){periodo_nf_sql}
            GROUP BY l.id, l.nome_fantasia
            ORDER BY ticket_medio DESC
            LIMIT 15
        """, (lojas_permitidas, *periodo_nf_params))

    desconto_resumo = query(f"""
        SELECT
            ROUND(SUM(valor_desconto)::numeric, 2) AS total_desconto,
            ROUND(SUM(valor_produtos)::numeric, 2) AS valor_bruto,
            ROUND(100.0 * SUM(valor_desconto) / NULLIF(SUM(valor_produtos), 0), 1) AS pct_bruto,
            COUNT(*) FILTER (WHERE valor_desconto > 0) AS notas_com_desconto,
            COUNT(*) AS total_notas
        FROM notas_fiscais nf
        WHERE nf.loja_id = ANY(%s){periodo_nf_sql}
    """, (lojas_permitidas, *periodo_nf_params), fetchall=False)

    desconto_categoria = query(f"""
        SELECT
            COALESCE(NULLIF(TRIM(p.categoria), ''), 'Sem categoria') AS categoria,
            ROUND(SUM(iv.valor_desconto)::numeric, 2) AS desconto_total,
            ROUND(SUM(iv.valor_total + iv.valor_desconto)::numeric, 2) AS valor_bruto,
            ROUND(100.0 * SUM(iv.valor_desconto) / NULLIF(SUM(iv.valor_total + iv.valor_desconto), 0), 1) AS pct_desconto,
            COUNT(DISTINCT nf.id) AS notas
        FROM itens_venda iv
        JOIN notas_fiscais nf ON iv.nota_id = nf.id
        JOIN produtos p ON iv.produto_id = p.id
        WHERE nf.loja_id = ANY(%s){periodo_sql}
          AND iv.valor_desconto > 0
        GROUP BY 1
        ORDER BY desconto_total DESC
        LIMIT 15
    """, (lojas_permitidas, *periodo_params))

    desconto_produto = query(f"""
        SELECT
            MAX(p.nome) AS produto,
            MAX(p.categoria) AS categoria,
            ROUND(SUM(iv.valor_desconto)::numeric, 2) AS desconto_total,
            ROUND(SUM(iv.valor_total + iv.valor_desconto)::numeric, 2) AS valor_bruto,
            ROUND(100.0 * SUM(iv.valor_desconto) / NULLIF(SUM(iv.valor_total + iv.valor_desconto), 0), 1) AS pct_desconto,
            COUNT(DISTINCT nf.id) AS notas
        FROM itens_venda iv
        JOIN notas_fiscais nf ON iv.nota_id = nf.id
        JOIN produtos p ON iv.produto_id = p.id
        WHERE nf.loja_id = ANY(%s){periodo_sql}
          AND iv.valor_desconto > 0
        GROUP BY LOWER(TRIM(p.nome))
        ORDER BY desconto_total DESC
        LIMIT 15
    """, (lojas_permitidas, *periodo_params))

    vendas_por_cidade = query(f"""
        SELECT
            COALESCE(NULLIF(TRIM(c.municipio), ''), 'Não informado') AS municipio,
            COALESCE(NULLIF(TRIM(c.uf), ''), '—') AS uf,
            COUNT(DISTINCT nf.id) AS notas,
            COUNT(DISTINCT nf.cliente_id) AS clientes,
            ROUND(SUM(nf.valor_total)::numeric, 2) AS faturamento,
            ROUND(AVG(nf.valor_total)::numeric, 2) AS ticket_medio
        FROM notas_fiscais nf
        JOIN clientes c ON nf.cliente_id = c.id
        WHERE nf.loja_id = ANY(%s){periodo_nf_sql}
        GROUP BY 1, 2
        ORDER BY faturamento DESC
        LIMIT 15
    """, (lojas_permitidas, *periodo_nf_params))

    resumo_desc = dict(desconto_resumo) if desconto_resumo else {}
    total_notas_desc = int(resumo_desc.get("total_notas") or 0)
    notas_com_desc = int(resumo_desc.get("notas_com_desconto") or 0)
    descontos_analise = {
        "resumo": {
            "total_desconto": float(resumo_desc.get("total_desconto") or 0),
            "valor_bruto": float(resumo_desc.get("valor_bruto") or 0),
            "pct_bruto": float(resumo_desc.get("pct_bruto") or 0),
            "notas_com_desconto": notas_com_desc,
            "pct_notas": round(100.0 * notas_com_desc / total_notas_desc, 1) if total_notas_desc else 0.0,
        },
        "por_categoria": [dict(r) for r in desconto_categoria],
        "por_produto": [dict(r) for r in desconto_produto],
    }

    return {
        "multi_loja": multi_loja,
        "total_lojas": len(lojas_permitidas),
        "sazonalidade": {
            "dia_semana": [dict(r) for r in dia_semana],
            "dia_mes": [dict(r) for r in dia_mes],
            "hora": [dict(r) for r in hora],
            "picos": picos,
        },
        "top_lojas": [dict(r) for r in top_lojas],
        "top_produtos_por_loja": [dict(r) for r in top_produtos_por_loja],
        "comportamento": {
            "clientes": _montar_clientes_novos_rec(clientes_tipos, sem_identificacao),
            "ticket_pagamento": [dict(r) for r in ticket_pagamento],
            "ticket_categoria": [dict(r) for r in ticket_categoria],
            "ticket_loja": [dict(r) for r in ticket_loja],
        },
        "descontos_analise": descontos_analise,
        "vendas_por_cidade": [dict(r) for r in vendas_por_cidade],
    }


@app.get("/api/analises/cestas")
def analises_cestas(
    loja_id: Optional[int] = None,
    data_inicio: Optional[str] = None,
    data_fim: Optional[str] = None,
    q: str = "",
    min_ocorrencias: int = 10,
    min_confianca: float = 0,
    limit: int = 50,
    current_user: dict = Depends(get_current_user),
):
    """Produtos frequentemente comprados juntos na mesma nota (venda casada).

    Pares são únicos (A+B = B+A), agrupados por nome normalizado do produto
    para evitar duplicatas de cadastro. Retorna confiança nos dois sentidos.
    """
    lojas_permitidas, periodo_sql, periodo_params, _, _, _ = _params_analises(
        loja_id, data_inicio, data_fim, current_user
    )
    min_ocorrencias = max(3, min(min_ocorrencias, 100))
    min_confianca = max(0, min(min_confianca, 100))
    limit = max(5, min(limit, 100))

    if not lojas_permitidas:
        return {"cestas": [], "total": 0, "min_ocorrencias": min_ocorrencias}

    filtro_nome = ""
    filtro_params: list = []
    if q:
        filtro_nome = " AND (LOWER(pa.produto_nome) LIKE LOWER(%s) OR LOWER(pb.produto_nome) LIKE LOWER(%s))"
        like = f"%{q}%"
        filtro_params.extend([like, like])

    sql = f"""
        WITH itens_norm AS (
            SELECT DISTINCT
                iv.nota_id,
                LOWER(TRIM(p.nome)) AS produto_key,
                p.nome AS produto_nome,
                p.categoria AS categoria
            FROM itens_venda iv
            JOIN produtos p ON p.id = iv.produto_id
            JOIN notas_fiscais nf ON iv.nota_id = nf.id
            WHERE nf.loja_id = ANY(%s){periodo_sql}
        ),
        produtos_agrupados AS (
            SELECT
                produto_key,
                MAX(produto_nome) AS produto_nome,
                MAX(categoria) AS categoria,
                COUNT(DISTINCT nota_id) AS notas_com_produto
            FROM itens_norm
            GROUP BY produto_key
        ),
        pares AS (
            SELECT
                i1.produto_key AS key_a,
                i2.produto_key AS key_b,
                COUNT(DISTINCT i1.nota_id) AS vezes_juntos
            FROM itens_norm i1
            JOIN itens_norm i2
                ON i1.nota_id = i2.nota_id AND i1.produto_key < i2.produto_key
            GROUP BY i1.produto_key, i2.produto_key
            HAVING COUNT(DISTINCT i1.nota_id) >= %s
        ),
        resultado AS (
            SELECT
                pa.produto_nome AS produto_a,
                pb.produto_nome AS produto_b,
                pa.categoria AS categoria_a,
                pb.categoria AS categoria_b,
                par.vezes_juntos,
                pa.notas_com_produto AS notas_com_a,
                pb.notas_com_produto AS notas_com_b,
                ROUND(100.0 * par.vezes_juntos / NULLIF(pa.notas_com_produto, 0), 1) AS confianca_a_para_b,
                ROUND(100.0 * par.vezes_juntos / NULLIF(pb.notas_com_produto, 0), 1) AS confianca_b_para_a
            FROM pares par
            JOIN produtos_agrupados pa ON pa.produto_key = par.key_a
            JOIN produtos_agrupados pb ON pb.produto_key = par.key_b
        )
        SELECT
            produto_a,
            produto_b,
            categoria_a,
            categoria_b,
            vezes_juntos,
            notas_com_a,
            notas_com_b,
            confianca_a_para_b,
            confianca_b_para_a,
            GREATEST(confianca_a_para_b, confianca_b_para_a) AS confianca_max
        FROM resultado
        WHERE GREATEST(confianca_a_para_b, confianca_b_para_a) >= %s{filtro_nome}
        ORDER BY vezes_juntos DESC, confianca_max DESC
        LIMIT %s
    """
    params = [
        lojas_permitidas, *periodo_params,
        min_ocorrencias,
        min_confianca,
        *filtro_params,
        limit,
    ]
    rows = query(sql, params)
    cestas = [dict(r) for r in rows]
    return {
        "cestas": cestas,
        "total": len(cestas),
        "min_ocorrencias": min_ocorrencias,
    }


# ─── Administração (Fases 1 e 2) ────────────────────────────

class FranquiaCreate(BaseModel):
    nome: str
    ativo: bool = True


class FranquiaUpdate(BaseModel):
    nome: Optional[str] = None
    ativo: Optional[bool] = None


class UsuarioCreate(BaseModel):
    nome: str
    email: str
    senha: str
    nivel_acesso: str
    ativo: bool = True
    loja_id: Optional[int] = None
    franquia_ids: Optional[List[int]] = None


class UsuarioUpdate(BaseModel):
    nome: Optional[str] = None
    email: Optional[str] = None
    nivel_acesso: Optional[str] = None
    ativo: Optional[bool] = None
    loja_id: Optional[int] = None


class SenhaUpdate(BaseModel):
    senha: str


class FranquiasUsuarioUpdate(BaseModel):
    franquia_ids: List[int]


class LojaFranquiaUpdate(BaseModel):
    franquia_id: Optional[int] = None


class UsuarioLojaUpdate(BaseModel):
    loja_id: int


@app.get("/api/admin/franquias")
def admin_listar_franquias(current_user: dict = Depends(get_current_user)):
    _require_admin(current_user)
    if current_user["nivel_acesso"] == "master":
        rows = query("""
            SELECT f.id, f.nome, f.ativo, f.criado_em,
                COUNT(DISTINCT l.id) FILTER (WHERE l.ativo = TRUE) AS total_lojas,
                COUNT(DISTINCT uf.usuario_id) AS total_franqueados
            FROM franquias f
            LEFT JOIN lojas l ON l.franquia_id = f.id
            LEFT JOIN usuario_franquias uf ON uf.franquia_id = f.id
            GROUP BY f.id
            ORDER BY f.nome
        """)
    else:
        rows = query("""
            SELECT f.id, f.nome, f.ativo, f.criado_em,
                COUNT(DISTINCT l.id) FILTER (WHERE l.ativo = TRUE) AS total_lojas,
                COUNT(DISTINCT uf.usuario_id) AS total_franqueados
            FROM franquias f
            LEFT JOIN lojas l ON l.franquia_id = f.id
            LEFT JOIN usuario_franquias uf ON uf.franquia_id = f.id
            WHERE f.id = ANY(%s)
            GROUP BY f.id
            ORDER BY f.nome
        """, (current_user["franquias"],))
    return {"franquias": [dict(r) for r in rows]}


@app.post("/api/admin/franquias")
def admin_criar_franquia(body: FranquiaCreate, current_user: dict = Depends(get_current_user)):
    _require_master(current_user)
    nome = body.nome.strip()
    if not nome:
        raise HTTPException(status_code=400, detail="Nome da franquia é obrigatório")
    row = query("""
        INSERT INTO franquias (nome, ativo)
        VALUES (%s, %s)
        RETURNING id, nome, ativo, criado_em
    """, (nome, body.ativo), fetchall=False)
    return {"franquia": dict(row)}


@app.put("/api/admin/franquias/{franquia_id}")
def admin_atualizar_franquia(
    franquia_id: int,
    body: FranquiaUpdate,
    current_user: dict = Depends(get_current_user),
):
    _require_master(current_user)
    atual = query("SELECT id FROM franquias WHERE id = %s", (franquia_id,), fetchall=False)
    if not atual:
        raise HTTPException(status_code=404, detail="Franquia não encontrada")
    campos = []
    params = []
    if body.nome is not None:
        nome = body.nome.strip()
        if not nome:
            raise HTTPException(status_code=400, detail="Nome inválido")
        campos.append("nome = %s")
        params.append(nome)
    if body.ativo is not None:
        campos.append("ativo = %s")
        params.append(body.ativo)
    if not campos:
        raise HTTPException(status_code=400, detail="Nenhum campo para atualizar")
    campos.append("atualizado_em = NOW()")
    params.append(franquia_id)
    row = query(f"""
        UPDATE franquias SET {', '.join(campos)}
        WHERE id = %s
        RETURNING id, nome, ativo, criado_em
    """, tuple(params), fetchall=False)
    return {"franquia": dict(row)}


@app.get("/api/admin/lojas")
def admin_listar_lojas(current_user: dict = Depends(get_current_user)):
    _require_admin(current_user)
    lojas_ids = _lojas_gestao_ids(current_user)
    if not lojas_ids:
        return {"lojas": []}
    if current_user["nivel_acesso"] == "master":
        rows = query("""
            SELECT l.id, l.cnpj, l.nome_fantasia, l.municipio, l.uf, l.ativo,
                l.franquia_id, COALESCE(f.nome, '—') AS franquia
            FROM lojas l
            LEFT JOIN franquias f ON f.id = l.franquia_id
            WHERE l.ativo = TRUE
            ORDER BY l.nome_fantasia
        """)
    else:
        rows = query("""
            SELECT l.id, l.cnpj, l.nome_fantasia, l.municipio, l.uf, l.ativo,
                l.franquia_id, COALESCE(f.nome, '—') AS franquia
            FROM lojas l
            LEFT JOIN franquias f ON f.id = l.franquia_id
            WHERE l.id = ANY(%s)
            ORDER BY l.nome_fantasia
        """, (lojas_ids,))
    return {"lojas": [dict(r) for r in rows]}


@app.put("/api/admin/lojas/{loja_id}/franquia")
def admin_vincular_loja_franquia(
    loja_id: int,
    body: LojaFranquiaUpdate,
    current_user: dict = Depends(get_current_user),
):
    _require_master(current_user)
    loja = query("SELECT id FROM lojas WHERE id = %s", (loja_id,), fetchall=False)
    if not loja:
        raise HTTPException(status_code=404, detail="Loja não encontrada")
    if body.franquia_id is not None:
        fr = query("SELECT id FROM franquias WHERE id = %s", (body.franquia_id,), fetchall=False)
        if not fr:
            raise HTTPException(status_code=404, detail="Franquia não encontrada")
    row = query("""
        UPDATE lojas SET franquia_id = %s, atualizado_em = NOW()
        WHERE id = %s
        RETURNING id, nome_fantasia, franquia_id
    """, (body.franquia_id, loja_id), fetchall=False)
    franquia_nome = None
    if body.franquia_id:
        fn = query("SELECT nome FROM franquias WHERE id = %s", (body.franquia_id,), fetchall=False)
        franquia_nome = fn["nome"] if fn else None
    return {"loja": dict(row), "franquia": franquia_nome}


@app.get("/api/admin/usuarios")
def admin_listar_usuarios(current_user: dict = Depends(get_current_user)):
    _require_admin(current_user)
    if current_user["nivel_acesso"] == "master":
        rows = query("""
            SELECT u.id, u.nome, u.email, u.nivel_acesso, u.ativo, u.loja_id,
                l.nome_fantasia AS loja_nome, u.franquia_id, u.criado_em,
                COALESCE(array_agg(DISTINCT uf.franquia_id) FILTER (WHERE uf.franquia_id IS NOT NULL), '{}') AS franquia_ids,
                COALESCE(array_agg(DISTINCT f.nome) FILTER (WHERE f.nome IS NOT NULL), '{}') AS franquias_nomes
            FROM usuarios u
            LEFT JOIN lojas l ON l.id = u.loja_id
            LEFT JOIN usuario_franquias uf ON uf.usuario_id = u.id
            LEFT JOIN franquias f ON f.id = uf.franquia_id
            GROUP BY u.id, l.nome_fantasia
            ORDER BY u.nome
        """)
    else:
        lojas_ids = _lojas_gestao_ids(current_user)
        if not lojas_ids:
            return {"usuarios": []}
        rows = query("""
            SELECT u.id, u.nome, u.email, u.nivel_acesso, u.ativo, u.loja_id,
                l.nome_fantasia AS loja_nome, u.franquia_id, u.criado_em,
                COALESCE(array_agg(DISTINCT uf.franquia_id) FILTER (WHERE uf.franquia_id IS NOT NULL), '{}') AS franquia_ids,
                COALESCE(array_agg(DISTINCT f.nome) FILTER (WHERE f.nome IS NOT NULL), '{}') AS franquias_nomes
            FROM usuarios u
            LEFT JOIN lojas l ON l.id = u.loja_id
            LEFT JOIN usuario_franquias uf ON uf.usuario_id = u.id
            LEFT JOIN franquias f ON f.id = uf.franquia_id
            WHERE u.nivel_acesso = 'operador' AND u.loja_id = ANY(%s)
            GROUP BY u.id, l.nome_fantasia
            ORDER BY u.nome
        """, (lojas_ids,))
    usuarios = []
    for r in rows:
        item = dict(r)
        item["franquia_ids"] = list(item.get("franquia_ids") or [])
        item["franquias_nomes"] = list(item.get("franquias_nomes") or [])
        usuarios.append(item)
    return {"usuarios": usuarios}


@app.post("/api/admin/usuarios")
def admin_criar_usuario(body: UsuarioCreate, current_user: dict = Depends(get_current_user)):
    _require_admin(current_user)
    nivel = body.nivel_acesso.strip().lower()
    if nivel not in ("master", "franqueado", "operador"):
        raise HTTPException(status_code=400, detail="Nível de acesso inválido")
    if current_user["nivel_acesso"] == "franqueado":
        if nivel != "operador":
            raise HTTPException(status_code=403, detail="Franqueado só pode criar operadores")
        _validar_loja_gestao(current_user, body.loja_id)
    if nivel == "operador":
        _validar_loja_gestao(current_user, body.loja_id)
    elif nivel == "franqueado":
        if not body.franquia_ids:
            raise HTTPException(status_code=400, detail="Franqueado precisa de ao menos uma franquia")
        _validar_franquias_gestao(current_user, body.franquia_ids)
    elif nivel == "master":
        _require_master(current_user)
    email = body.email.strip().lower()
    if not email or len(body.senha) < 6:
        raise HTTPException(status_code=400, detail="E-mail inválido ou senha com menos de 6 caracteres")
    dup = query("SELECT id FROM usuarios WHERE LOWER(email) = LOWER(%s)", (email,), fetchall=False)
    if dup:
        raise HTTPException(status_code=409, detail="E-mail já cadastrado")
    senha_hash = hash_password(body.senha)
    loja_id = body.loja_id if nivel == "operador" else None
    franquia_id = body.franquia_ids[0] if nivel == "franqueado" and body.franquia_ids else None
    row = query("""
        INSERT INTO usuarios (nome, email, senha_hash, nivel_acesso, ativo, loja_id, franquia_id)
        VALUES (%s, %s, %s, %s, %s, %s, %s)
        RETURNING id, nome, email, nivel_acesso, ativo, loja_id, franquia_id, criado_em
    """, (body.nome.strip(), email, senha_hash, nivel, body.ativo, loja_id, franquia_id), fetchall=False)
    if nivel == "franqueado" and body.franquia_ids:
        _sync_usuario_franquias(row["id"], body.franquia_ids)
    return {"usuario": dict(row)}


@app.put("/api/admin/usuarios/{usuario_id}")
def admin_atualizar_usuario(
    usuario_id: int,
    body: UsuarioUpdate,
    current_user: dict = Depends(get_current_user),
):
    _require_admin(current_user)
    alvo = _usuario_pode_gestao(current_user, usuario_id)
    if current_user["nivel_acesso"] == "franqueado" and body.nivel_acesso and body.nivel_acesso != "operador":
        raise HTTPException(status_code=403, detail="Franqueado só pode manter perfil operador")
    if body.nivel_acesso == "master":
        _require_master(current_user)
    campos = []
    params = []
    if body.nome is not None:
        campos.append("nome = %s")
        params.append(body.nome.strip())
    if body.email is not None:
        email = body.email.strip().lower()
        dup = query(
            "SELECT id FROM usuarios WHERE LOWER(email) = LOWER(%s) AND id <> %s",
            (email, usuario_id),
            fetchall=False,
        )
        if dup:
            raise HTTPException(status_code=409, detail="E-mail já cadastrado")
        campos.append("email = %s")
        params.append(email)
    novo_nivel = body.nivel_acesso.strip().lower() if body.nivel_acesso else alvo["nivel_acesso"]
    if body.nivel_acesso is not None:
        if novo_nivel not in ("master", "franqueado", "operador"):
            raise HTTPException(status_code=400, detail="Nível de acesso inválido")
        campos.append("nivel_acesso = %s")
        params.append(novo_nivel)
    if body.ativo is not None:
        if usuario_id == current_user["id"] and not body.ativo:
            raise HTTPException(status_code=400, detail="Você não pode desativar sua própria conta")
        campos.append("ativo = %s")
        params.append(body.ativo)
    if body.loja_id is not None or novo_nivel == "operador":
        loja_id = body.loja_id if body.loja_id is not None else alvo["loja_id"]
        if novo_nivel == "operador":
            _validar_loja_gestao(current_user, loja_id)
        campos.append("loja_id = %s")
        params.append(loja_id if novo_nivel == "operador" else None)
    if novo_nivel != "operador" and body.nivel_acesso is not None:
        if "loja_id = %s" not in campos:
            campos.append("loja_id = %s")
            params.append(None)
    if not campos:
        raise HTTPException(status_code=400, detail="Nenhum campo para atualizar")
    campos.append("atualizado_em = NOW()")
    params.append(usuario_id)
    row = query(f"""
        UPDATE usuarios SET {', '.join(campos)}
        WHERE id = %s
        RETURNING id, nome, email, nivel_acesso, ativo, loja_id, franquia_id, criado_em
    """, tuple(params), fetchall=False)
    return {"usuario": dict(row)}


@app.put("/api/admin/usuarios/{usuario_id}/senha")
def admin_atualizar_senha(
    usuario_id: int,
    body: SenhaUpdate,
    current_user: dict = Depends(get_current_user),
):
    _require_admin(current_user)
    _usuario_pode_gestao(current_user, usuario_id)
    if len(body.senha) < 6:
        raise HTTPException(status_code=400, detail="Senha deve ter ao menos 6 caracteres")
    senha_hash = hash_password(body.senha)
    query(
        "UPDATE usuarios SET senha_hash = %s, atualizado_em = NOW() WHERE id = %s",
        (senha_hash, usuario_id),
        fetchall=False,
    )
    return {"status": "ok"}


@app.put("/api/admin/usuarios/{usuario_id}/franquias")
def admin_vincular_usuario_franquias(
    usuario_id: int,
    body: FranquiasUsuarioUpdate,
    current_user: dict = Depends(get_current_user),
):
    _require_master(current_user)
    alvo = _usuario_pode_gestao(current_user, usuario_id)
    if alvo["nivel_acesso"] != "franqueado":
        raise HTTPException(status_code=400, detail="Vínculo de franquias só se aplica a usuários franqueados")
    if not body.franquia_ids:
        raise HTTPException(status_code=400, detail="Informe ao menos uma franquia")
    for fid in body.franquia_ids:
        fr = query("SELECT id FROM franquias WHERE id = %s AND ativo = TRUE", (fid,), fetchall=False)
        if not fr:
            raise HTTPException(status_code=404, detail=f"Franquia {fid} não encontrada")
    _sync_usuario_franquias(usuario_id, body.franquia_ids)
    return {"status": "ok", "franquia_ids": body.franquia_ids}


@app.put("/api/admin/usuarios/{usuario_id}/loja")
def admin_vincular_usuario_loja(
    usuario_id: int,
    body: UsuarioLojaUpdate,
    current_user: dict = Depends(get_current_user),
):
    _require_admin(current_user)
    alvo = _usuario_pode_gestao(current_user, usuario_id)
    if alvo["nivel_acesso"] != "operador":
        raise HTTPException(status_code=400, detail="Vínculo de loja só se aplica a operadores")
    _validar_loja_gestao(current_user, body.loja_id)
    row = query("""
        UPDATE usuarios SET loja_id = %s, atualizado_em = NOW()
        WHERE id = %s
        RETURNING id, nome, loja_id
    """, (body.loja_id, usuario_id), fetchall=False)
    loja = query("SELECT nome_fantasia FROM lojas WHERE id = %s", (body.loja_id,), fetchall=False)
    return {"usuario": dict(row), "loja_nome": loja["nome_fantasia"] if loja else None}
