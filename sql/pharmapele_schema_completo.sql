-- =====================================================
-- PHARMAPELE - Estrutura Completa do Banco de Dados
-- Use este script para criar o banco de dados na Web / Produção
-- =====================================================

-- =====================================================
-- 1. TABELA: clientes
-- =====================================================
CREATE TABLE IF NOT EXISTS clientes (
    id SERIAL PRIMARY KEY,
    cpf VARCHAR(14) UNIQUE NOT NULL,
    nome VARCHAR(255) NOT NULL,
    telefone VARCHAR(20),
    email VARCHAR(255),
    cep VARCHAR(10),
    municipio VARCHAR(100),
    uf VARCHAR(2),
    criado_em TIMESTAMP DEFAULT NOW(),
    atualizado_em TIMESTAMP DEFAULT NOW()
);

-- =====================================================
-- 2. TABELA: franquias
-- =====================================================
CREATE TABLE IF NOT EXISTS franquias (
    id SERIAL PRIMARY KEY,
    nome VARCHAR(255) NOT NULL,
    ativo BOOLEAN DEFAULT TRUE,
    criado_em TIMESTAMP DEFAULT NOW(),
    atualizado_em TIMESTAMP DEFAULT NOW()
);

-- =====================================================
-- 3. TABELA: lojas
-- =====================================================
CREATE TABLE IF NOT EXISTS lojas (
    id SERIAL PRIMARY KEY,
    cnpj VARCHAR(18) UNIQUE NOT NULL,
    nome_fantasia VARCHAR(255),
    razao_social VARCHAR(255),
    endereco VARCHAR(500),
    municipio VARCHAR(100),
    uf VARCHAR(2),
    telefone VARCHAR(20),
    ativo BOOLEAN DEFAULT TRUE,
    franquia_id INTEGER REFERENCES franquias(id) ON DELETE SET NULL,
    criado_em TIMESTAMP DEFAULT NOW(),
    atualizado_em TIMESTAMP DEFAULT NOW()
);

-- =====================================================
-- 4. TABELA: produtos
-- =====================================================
CREATE TABLE IF NOT EXISTS produtos (
    id SERIAL PRIMARY KEY,
    codigo VARCHAR(50) NOT NULL,
    nome VARCHAR(500) NOT NULL,
    ncm VARCHAR(20),
    categoria VARCHAR(100),
    unidade VARCHAR(10) DEFAULT 'UN',
    preco_atual NUMERIC(10,2),
    loja_id INTEGER REFERENCES lojas(id) ON DELETE SET NULL,
    criado_em TIMESTAMP DEFAULT NOW(),
    atualizado_em TIMESTAMP DEFAULT NOW(),
    CONSTRAINT unique_codigo_loja UNIQUE (codigo, loja_id)
);

-- =====================================================
-- 5. TABELA: notas_fiscais
-- =====================================================
CREATE TABLE IF NOT EXISTS notas_fiscais (
    id SERIAL PRIMARY KEY,
    chave_nfe VARCHAR(50) UNIQUE NOT NULL,
    numero_nf VARCHAR(20),
    serie VARCHAR(10),
    data_emissao TIMESTAMP NOT NULL,
    cliente_id INTEGER REFERENCES clientes(id) ON DELETE SET NULL,
    loja_id INTEGER REFERENCES lojas(id) ON DELETE SET NULL,
    valor_produtos NUMERIC(10,2),
    valor_desconto NUMERIC(10,2),
    valor_total NUMERIC(10,2),
    forma_pagamento VARCHAR(50),
    xml_filename VARCHAR(255),
    importado_em TIMESTAMP DEFAULT NOW()
);

-- =====================================================
-- 6. TABELA: itens_venda
-- =====================================================
CREATE TABLE IF NOT EXISTS itens_venda (
    id SERIAL PRIMARY KEY,
    nota_id INTEGER REFERENCES notas_fiscais(id) ON DELETE CASCADE,
    produto_id INTEGER REFERENCES produtos(id) ON DELETE RESTRICT,
    quantidade NUMERIC(10,4) NOT NULL,
    valor_unitario NUMERIC(10,2) NOT NULL,
    valor_desconto NUMERIC(10,2) DEFAULT 0,
    valor_total NUMERIC(10,2) NOT NULL
);

-- =====================================================
-- 7. TABELA: alertas_recompra
-- =====================================================
CREATE TABLE IF NOT EXISTS alertas_recompra (
    id SERIAL PRIMARY KEY,
    cliente_id INTEGER REFERENCES clientes(id) ON DELETE CASCADE,
    produto_id INTEGER REFERENCES produtos(id) ON DELETE CASCADE,
    ultima_compra DATE,
    intervalo_medio_dias INTEGER,
    proxima_compra_estimada DATE,
    alerta_enviado BOOLEAN DEFAULT FALSE,
    alerta_enviado_em TIMESTAMP,
    loja_id INTEGER REFERENCES lojas(id) ON DELETE SET NULL,
    criado_em TIMESTAMP DEFAULT NOW(),
    CONSTRAINT unique_cliente_produto_loja UNIQUE (cliente_id, produto_id, loja_id)
);

-- =====================================================
-- 8. TABELA: usuarios
-- =====================================================
CREATE TABLE IF NOT EXISTS usuarios (
    id SERIAL PRIMARY KEY,
    nome VARCHAR(255) NOT NULL,
    email VARCHAR(255) UNIQUE NOT NULL,
    senha_hash VARCHAR(255) NOT NULL,
    nivel_acesso VARCHAR(20) NOT NULL CHECK (nivel_acesso IN ('master', 'franqueado', 'operador')),
    franquia_id INTEGER REFERENCES franquias(id) ON DELETE SET NULL,
    loja_id INTEGER REFERENCES lojas(id) ON DELETE SET NULL,
    ativo BOOLEAN DEFAULT TRUE,
    criado_em TIMESTAMP DEFAULT NOW(),
    atualizado_em TIMESTAMP DEFAULT NOW()
);

-- =====================================================
-- 8.1. TABELA: usuario_franquias
-- =====================================================
CREATE TABLE IF NOT EXISTS usuario_franquias (
    usuario_id INTEGER REFERENCES usuarios(id) ON DELETE CASCADE,
    franquia_id INTEGER REFERENCES franquias(id) ON DELETE CASCADE,
    criado_em TIMESTAMP DEFAULT NOW(),
    PRIMARY KEY (usuario_id, franquia_id)
);

-- =====================================================
-- 9. ÍNDICES para performance
-- =====================================================
CREATE INDEX IF NOT EXISTS idx_nf_cliente ON notas_fiscais(cliente_id);
CREATE INDEX IF NOT EXISTS idx_nf_data ON notas_fiscais(data_emissao);
CREATE INDEX IF NOT EXISTS idx_nf_loja ON notas_fiscais(loja_id);
CREATE INDEX IF NOT EXISTS idx_itens_nota ON itens_venda(nota_id);
CREATE INDEX IF NOT EXISTS idx_itens_produto ON itens_venda(produto_id);
CREATE INDEX IF NOT EXISTS idx_alertas_cliente ON alertas_recompra(cliente_id);
CREATE INDEX IF NOT EXISTS idx_alertas_data ON alertas_recompra(proxima_compra_estimada);
CREATE INDEX IF NOT EXISTS idx_alertas_loja ON alertas_recompra(loja_id);
CREATE INDEX IF NOT EXISTS idx_lojas_franquia ON lojas(franquia_id);
CREATE INDEX IF NOT EXISTS idx_usuarios_email ON usuarios(email);
CREATE INDEX IF NOT EXISTS idx_usuarios_franquia ON usuarios(franquia_id);
CREATE INDEX IF NOT EXISTS idx_usuarios_loja ON usuarios(loja_id);
CREATE INDEX IF NOT EXISTS idx_usuario_franquias_usuario ON usuario_franquias(usuario_id);
CREATE INDEX IF NOT EXISTS idx_usuario_franquias_franquia ON usuario_franquias(franquia_id);

-- =====================================================
-- 10. VIEWS
-- =====================================================

-- View: vw_vendas_detalhadas
CREATE OR REPLACE VIEW vw_vendas_detalhadas AS
SELECT
    nf.id AS nota_id,
    nf.numero_nf,
    nf.data_emissao,
    c.cpf,
    COALESCE(c.nome, 'Consumidor Não Identificado') AS cliente,
    c.telefone,
    p.codigo AS cod_produto,
    p.nome AS produto,
    p.categoria,
    iv.quantidade,
    iv.valor_unitario,
    iv.valor_desconto,
    iv.valor_total,
    nf.valor_total AS total_nota,
    nf.xml_filename,
    nf.loja_id
FROM itens_venda iv
JOIN notas_fiscais nf ON iv.nota_id = nf.id
LEFT JOIN clientes c ON nf.cliente_id = c.id
JOIN produtos p ON iv.produto_id = p.id;

-- View: vw_frequencia_recompra
CREATE OR REPLACE VIEW vw_frequencia_recompra AS
SELECT
    c.id AS cliente_id,
    c.nome AS cliente,
    c.telefone,
    p.id AS produto_id,
    p.nome AS produto,
    nf.loja_id,
    COUNT(DISTINCT nf.id) AS total_compras,
    SUM(iv.quantidade) AS total_unidades,
    MIN(nf.data_emissao) AS primeira_compra,
    MAX(nf.data_emissao) AS ultima_compra,
    ROUND(
        EXTRACT(EPOCH FROM (MAX(nf.data_emissao) - MIN(nf.data_emissao))) / 86400.0
        / NULLIF(COUNT(DISTINCT nf.id) - 1, 0)
    ) AS intervalo_medio_dias,
    MAX(nf.data_emissao) + (
        INTERVAL '1 day' * ROUND(
            EXTRACT(EPOCH FROM (MAX(nf.data_emissao) - MIN(nf.data_emissao))) / 86400.0
            / NULLIF(COUNT(DISTINCT nf.id) - 1, 0)
        )
    ) AS proxima_compra_estimada
FROM itens_venda iv
JOIN notas_fiscais nf ON iv.nota_id = nf.id
JOIN clientes c ON nf.cliente_id = c.id
JOIN produtos p ON iv.produto_id = p.id
GROUP BY c.id, c.nome, c.telefone, p.id, p.nome, nf.loja_id
HAVING COUNT(DISTINCT nf.id) >= 2;

-- View: vw_clientes_inativos
CREATE OR REPLACE VIEW vw_clientes_inativos AS
SELECT
    c.id,
    c.nome,
    c.telefone,
    nf.loja_id,
    MAX(nf.data_emissao) AS ultima_compra,
    EXTRACT(DAY FROM NOW() - MAX(nf.data_emissao)) AS dias_sem_comprar,
    COUNT(DISTINCT nf.id) AS total_notas,
    SUM(nf.valor_total) AS total_gasto
FROM clientes c
JOIN notas_fiscais nf ON c.id = nf.cliente_id
GROUP BY c.id, c.nome, c.telefone, nf.loja_id
HAVING MAX(nf.data_emissao) < NOW() - INTERVAL '60 days';

-- =====================================================
-- 11. COMENTÁRIOS
-- =====================================================
COMMENT ON TABLE clientes IS 'Clientes extraídos das NFC-e';
COMMENT ON TABLE produtos IS 'Produtos vendidos';
COMMENT ON TABLE notas_fiscais IS 'Cabeçalho das NFC-e importadas';
COMMENT ON TABLE itens_venda IS 'Itens de cada NFC-e';
COMMENT ON TABLE alertas_recompra IS 'Controle de alertas de recompra por cliente/produto';
COMMENT ON TABLE lojas IS 'Lojas cadastradas a partir do emitente da NFC-e';
COMMENT ON TABLE franquias IS 'Franquias registradas que gerenciam uma ou mais lojas';
COMMENT ON TABLE usuarios IS 'Usuários com acesso ao sistema segregados por nível de acesso';

-- =====================================================
-- 12. DADOS INICIAIS (SEEDING)
-- =====================================================

-- Inserir Franquia padrão (Franquia Matriz)
INSERT INTO franquias (id, nome, ativo)
VALUES (1, 'Franquia Matriz', TRUE)
ON CONFLICT (id) DO NOTHING;

-- Inserir Loja padrão (Matriz) para receber registros antigos
INSERT INTO lojas (id, cnpj, nome_fantasia, razao_social, endereco, municipio, uf, telefone, franquia_id)
VALUES (1, '00000000000000', 'Matriz', 'Pharmapele Matriz Ltda', 'Avenida Principal, 100 - Centro', 'Recife', 'PE', '', 1)
ON CONFLICT (cnpj) DO NOTHING;

-- Garantir que as sequências SERIAL iniciem corretamente após inserções manuais de IDs
SELECT setval(pg_get_serial_sequence('franquias', 'id'), COALESCE(MAX(id), 1)) FROM franquias;
SELECT setval(pg_get_serial_sequence('lojas', 'id'), COALESCE(MAX(id), 1)) FROM lojas;

-- Inserir usuário Administrador Master padrão
-- Email: admin@pharmapele.com.br
-- Senha em texto plano: Ojuara10*
-- Hash bcrypt seguro pré-calculado
INSERT INTO usuarios (nome, email, senha_hash, nivel_acesso, franquia_id, loja_id, ativo)
VALUES (
    'Administrador Master',
    'admin@pharmapele.com.br',
    '$2b$12$X1/avgCxABbjZcTi3EHRxuZJaxWyvY9TZ7krjNunwfR6jnwkVPMW.',
    'master',
    1,
    NULL,
    TRUE
)
ON CONFLICT (email) DO NOTHING;

-- Associar administrador padrão à Franquia Matriz na tabela de junção muitos-para-muitos
INSERT INTO usuario_franquias (usuario_id, franquia_id)
SELECT id, 1 
FROM usuarios 
WHERE email = 'admin@pharmapele.com.br'
ON CONFLICT DO NOTHING;
