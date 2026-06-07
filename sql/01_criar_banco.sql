-- =====================================================
-- PHARMAPELE - Script de Criação do Banco de Dados
-- Execute no pgAdmin ou psql
-- =====================================================

-- Criar banco (execute separado no psql como superusuário)
-- CREATE DATABASE pharmapele;

-- Conecte ao banco pharmapele antes de continuar

-- =====================================================
-- TABELA: clientes
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
-- TABELA: produtos
-- =====================================================
CREATE TABLE IF NOT EXISTS produtos (
    id SERIAL PRIMARY KEY,
    codigo VARCHAR(50) UNIQUE NOT NULL,
    nome VARCHAR(500) NOT NULL,
    ncm VARCHAR(20),
    categoria VARCHAR(100), -- será inferida pelo NCM
    unidade VARCHAR(10) DEFAULT 'UN',
    preco_atual NUMERIC(10,2),
    criado_em TIMESTAMP DEFAULT NOW(),
    atualizado_em TIMESTAMP DEFAULT NOW()
);

-- =====================================================
-- TABELA: notas_fiscais
-- =====================================================
CREATE TABLE IF NOT EXISTS notas_fiscais (
    id SERIAL PRIMARY KEY,
    chave_nfe VARCHAR(50) UNIQUE NOT NULL,
    numero_nf VARCHAR(20),
    serie VARCHAR(10),
    data_emissao TIMESTAMP NOT NULL,
    cliente_id INTEGER REFERENCES clientes(id),
    valor_produtos NUMERIC(10,2),
    valor_desconto NUMERIC(10,2),
    valor_total NUMERIC(10,2),
    forma_pagamento VARCHAR(50),
    xml_filename VARCHAR(255),
    importado_em TIMESTAMP DEFAULT NOW()
);

-- =====================================================
-- TABELA: itens_venda
-- =====================================================
CREATE TABLE IF NOT EXISTS itens_venda (
    id SERIAL PRIMARY KEY,
    nota_id INTEGER REFERENCES notas_fiscais(id) ON DELETE CASCADE,
    produto_id INTEGER REFERENCES produtos(id),
    quantidade NUMERIC(10,4) NOT NULL,
    valor_unitario NUMERIC(10,2) NOT NULL,
    valor_desconto NUMERIC(10,2) DEFAULT 0,
    valor_total NUMERIC(10,2) NOT NULL
);

-- =====================================================
-- TABELA: alertas_recompra (para futuro módulo WhatsApp)
-- =====================================================
CREATE TABLE IF NOT EXISTS alertas_recompra (
    id SERIAL PRIMARY KEY,
    cliente_id INTEGER REFERENCES clientes(id),
    produto_id INTEGER REFERENCES produtos(id),
    ultima_compra DATE,
    intervalo_medio_dias INTEGER,
    proxima_compra_estimada DATE,
    alerta_enviado BOOLEAN DEFAULT FALSE,
    alerta_enviado_em TIMESTAMP,
    criado_em TIMESTAMP DEFAULT NOW(),
    UNIQUE(cliente_id, produto_id)
);

-- =====================================================
-- ÍNDICES para performance
-- =====================================================
CREATE INDEX IF NOT EXISTS idx_nf_cliente ON notas_fiscais(cliente_id);
CREATE INDEX IF NOT EXISTS idx_nf_data ON notas_fiscais(data_emissao);
CREATE INDEX IF NOT EXISTS idx_itens_nota ON itens_venda(nota_id);
CREATE INDEX IF NOT EXISTS idx_itens_produto ON itens_venda(produto_id);
CREATE INDEX IF NOT EXISTS idx_alertas_cliente ON alertas_recompra(cliente_id);
CREATE INDEX IF NOT EXISTS idx_alertas_data ON alertas_recompra(proxima_compra_estimada);

-- =====================================================
-- VIEWS úteis
-- =====================================================

-- View: vendas completas (nota + cliente + itens + produto)
CREATE OR REPLACE VIEW vw_vendas_detalhadas AS
SELECT
    nf.id AS nota_id,
    nf.numero_nf,
    nf.data_emissao,
    c.cpf,
    c.nome AS cliente,
    c.telefone,
    p.codigo AS cod_produto,
    p.nome AS produto,
    p.categoria,
    iv.quantidade,
    iv.valor_unitario,
    iv.valor_desconto,
    iv.valor_total,
    nf.valor_total AS total_nota,
    nf.xml_filename
FROM itens_venda iv
JOIN notas_fiscais nf ON iv.nota_id = nf.id
JOIN clientes c ON nf.cliente_id = c.id
JOIN produtos p ON iv.produto_id = p.id;

-- View: frequência de compra por cliente/produto
CREATE OR REPLACE VIEW vw_frequencia_recompra AS
SELECT
    c.id AS cliente_id,
    c.nome AS cliente,
    c.telefone,
    p.id AS produto_id,
    p.nome AS produto,
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
GROUP BY c.id, c.nome, c.telefone, p.id, p.nome
HAVING COUNT(DISTINCT nf.id) >= 2;

-- View: clientes inativos (sem compra há mais de 60 dias)
CREATE OR REPLACE VIEW vw_clientes_inativos AS
SELECT
    c.id,
    c.nome,
    c.telefone,
    MAX(nf.data_emissao) AS ultima_compra,
    EXTRACT(DAY FROM NOW() - MAX(nf.data_emissao)) AS dias_sem_comprar,
    COUNT(DISTINCT nf.id) AS total_notas,
    SUM(nf.valor_total) AS total_gasto
FROM clientes c
JOIN notas_fiscais nf ON c.id = nf.cliente_id
GROUP BY c.id, c.nome, c.telefone
HAVING MAX(nf.data_emissao) < NOW() - INTERVAL '60 days';

COMMENT ON TABLE clientes IS 'Clientes extraídos das NFC-e';
COMMENT ON TABLE produtos IS 'Produtos vendidos';
COMMENT ON TABLE notas_fiscais IS 'Cabeçalho das NFC-e importadas';
COMMENT ON TABLE itens_venda IS 'Itens de cada NFC-e';
COMMENT ON TABLE alertas_recompra IS 'Controle de alertas de recompra por cliente/produto';
