"""
PHARMAPELE - Backend API
FastAPI + PostgreSQL (Multi-lojas & Conectividade Melhorada)
"""

from fastapi import FastAPI, UploadFile, File, HTTPException, Depends
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
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
from typing import Optional

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


class LoginRequest(BaseModel):
    email: str
    senha: str


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
def dashboard(loja_id: Optional[int] = None, current_user: dict = Depends(get_current_user)):
    """KPIs principais para o dashboard, filtráveis por lojas permitidas"""
    lojas_permitidas = aplicar_filtro_segurança(current_user, loja_id)
    if not lojas_permitidas:
        return {
            "geral": {},
            "mes_atual": {},
            "alertas_recompra": 0,
            "clientes_inativos": 0,
        }

    kpis_sql = """
        SELECT
            COUNT(DISTINCT id) AS total_notas,
            COUNT(DISTINCT cliente_id) AS total_clientes,
            ROUND(SUM(valor_total)::numeric, 2) AS faturamento_total,
            ROUND(AVG(valor_total)::numeric, 2) AS ticket_medio,
            ROUND(SUM(valor_desconto)::numeric, 2) AS total_descontos
        FROM notas_fiscais
        WHERE loja_id = ANY(%s)
    """
    kpis = query(kpis_sql, (lojas_permitidas,), fetchall=False)

    mes_sql = """
        SELECT
            COUNT(DISTINCT id) AS notas_mes,
            COUNT(DISTINCT cliente_id) AS clientes_mes,
            ROUND(SUM(valor_total)::numeric, 2) AS faturamento_mes
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

    return {
        "geral": dict(kpis) if kpis else {},
        "mes_atual": dict(mes_atual) if mes_atual else {},
        "alertas_recompra": dict(alertas)["total"] if alertas else 0,
        "clientes_inativos": dict(inativos)["total"] if inativos else 0,
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
            COUNT(DISTINCT id) AS notas,
            COUNT(DISTINCT cliente_id) AS clientes
        FROM notas_fiscais
        WHERE loja_id = ANY(%s)
        GROUP BY DATE_TRUNC('month', data_emissao)
        ORDER BY mes_dt
    """
    rows = query(sql, (lojas_permitidas,))
    return [dict(r) for r in rows]


@app.get("/api/produtos-mais-vendidos")
def produtos_mais_vendidos(limit: int = 10, loja_id: Optional[int] = None, current_user: dict = Depends(get_current_user)):
    """Produtos mais vendidos, filtráveis por lojas permitidas"""
    lojas_permitidas = aplicar_filtro_segurança(current_user, loja_id)
    if not lojas_permitidas:
        return []
        
    sql = """
        SELECT
            p.nome,
            p.categoria,
            SUM(iv.quantidade) AS total_unidades,
            ROUND(SUM(iv.valor_total)::numeric, 2) AS receita,
            COUNT(DISTINCT nf.cliente_id) AS clientes_distintos
        FROM itens_venda iv
        JOIN produtos p ON iv.produto_id = p.id
        JOIN notas_fiscais nf ON iv.nota_id = nf.id
        WHERE nf.loja_id = ANY(%s)
        GROUP BY p.id, p.nome, p.categoria
        ORDER BY total_unidades DESC
        LIMIT %s
    """
    rows = query(sql, (lojas_permitidas, limit))
    return [dict(r) for r in rows]


@app.get("/api/alertas-recompra")
def alertas_recompra(dias: int = 7, loja_id: Optional[int] = None, current_user: dict = Depends(get_current_user)):
    """Alertas de recompra baseados em histórico, filtrados por lojas permitidas"""
    lojas_permitidas = aplicar_filtro_segurança(current_user, loja_id)
    if not lojas_permitidas:
        return []
        
    sql = """
        SELECT
            cliente,
            telefone,
            produto,
            total_compras,
            ultima_compra,
            intervalo_medio_dias,
            proxima_compra_estimada,
            ROUND(EXTRACT(DAY FROM proxima_compra_estimada - NOW())) AS dias_restantes
        FROM vw_frequencia_recompra
        WHERE proxima_compra_estimada BETWEEN NOW() - INTERVAL '3 days' AND NOW() + (INTERVAL '1 day' * %s)
          AND loja_id = ANY(%s)
        ORDER BY proxima_compra_estimada
    """
    rows = query(sql, (dias, lojas_permitidas))
    return [dict(r) for r in rows]


@app.get("/api/clientes-inativos")
def clientes_inativos(loja_id: Optional[int] = None, current_user: dict = Depends(get_current_user)):
    """Clientes inativos por lojas permitidas"""
    lojas_permitidas = aplicar_filtro_segurança(current_user, loja_id)
    if not lojas_permitidas:
        return []
        
    sql = "SELECT * FROM vw_clientes_inativos WHERE loja_id = ANY(%s) ORDER BY dias_sem_comprar DESC LIMIT 50"
    rows = query(sql, (lojas_permitidas,))
    return [dict(r) for r in rows]


@app.get("/api/clientes")
def listar_clientes(q: str = "", loja_id: Optional[int] = None, current_user: dict = Depends(get_current_user)):
    """Lista clientes com consolidado de compras. Filtro por lojas permitidas."""
    lojas_permitidas = aplicar_filtro_segurança(current_user, loja_id)
    if not lojas_permitidas:
        return []
        
    sql = """
        SELECT c.id, c.nome, c.cpf, c.telefone,
            COUNT(DISTINCT nf.id) AS total_compras,
            ROUND(SUM(nf.valor_total)::numeric, 2) AS total_gasto,
            MAX(nf.data_emissao) AS ultima_compra
        FROM clientes c
        LEFT JOIN notas_fiscais nf ON c.id = nf.cliente_id
        WHERE nf.loja_id = ANY(%s)
    """
    params = [lojas_permitidas]
    
    if q:
        sql += " AND (LOWER(c.nome) LIKE LOWER(%s) OR c.cpf LIKE %s)"
        params.extend([f"%{q}%", f"%{q}%"])
        
    sql += " GROUP BY c.id, c.nome, c.cpf, c.telefone"
    sql += " ORDER BY total_gasto DESC NULLS LAST LIMIT " + ("30" if q else "50")
    
    rows = query(sql, params)
    return [dict(r) for r in rows]


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

    return {
        "cliente": dict(cliente),
        "notas": [dict(r) for r in notas],
        "produtos": [dict(r) for r in produtos_comprados],
        "recompras_previstas": [dict(r) for r in recompras],
    }


@app.get("/api/vendas-por-categoria")
def vendas_categoria(loja_id: Optional[int] = None, current_user: dict = Depends(get_current_user)):
    """Gráfico de pizza por categoria, filtrável por lojas permitidas"""
    lojas_permitidas = aplicar_filtro_segurança(current_user, loja_id)
    if not lojas_permitidas:
        return []
        
    sql = """
        SELECT p.categoria,
            ROUND(SUM(iv.valor_total)::numeric, 2) AS receita,
            SUM(iv.quantidade) AS unidades
        FROM itens_venda iv
        JOIN produtos p ON iv.produto_id = p.id
        JOIN notas_fiscais nf ON iv.nota_id = nf.id
        WHERE nf.loja_id = ANY(%s)
        GROUP BY p.categoria ORDER BY receita DESC
    """
    rows = query(sql, (lojas_permitidas,))
    return [dict(r) for r in rows]


@app.get("/api/notas")
def listar_notas(limit: int = 20, offset: int = 0, loja_id: Optional[int] = None, current_user: dict = Depends(get_current_user)):
    """Lista as notas fiscais importadas com indicação de loja de origem, filtrada por lojas permitidas"""
    lojas_permitidas = aplicar_filtro_segurança(current_user, loja_id)
    if not lojas_permitidas:
        return {"notas": [], "total": 0}
        
    sql = """
        SELECT nf.numero_nf, nf.data_emissao, COALESCE(c.nome, 'Consumidor Não Identificado') AS cliente,
            nf.valor_total, nf.forma_pagamento, nf.xml_filename, l.nome_fantasia AS loja
        FROM notas_fiscais nf
        LEFT JOIN clientes c ON nf.cliente_id = c.id
        JOIN lojas l ON nf.loja_id = l.id
        WHERE nf.loja_id = ANY(%s)
        ORDER BY nf.data_emissao DESC LIMIT %s OFFSET %s
    """
    rows = query(sql, (lojas_permitidas, limit, offset))
    
    count_sql = "SELECT COUNT(*) AS t FROM notas_fiscais WHERE loja_id = ANY(%s)"
    total = query(count_sql, (lojas_permitidas,), fetchall=False)
    
    return {"notas": [dict(r) for r in rows], "total": dict(total)["t"]}
