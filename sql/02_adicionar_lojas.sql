-- =====================================================
-- PHARMAPELE - Script de Migração: Suporte a Múltiplas Lojas
-- Execute no pgAdmin ou psql conectado ao banco pharmapele (ou pharmapel)
-- =====================================================

-- 1. Criar tabela: lojas
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
    criado_em TIMESTAMP DEFAULT NOW(),
    atualizado_em TIMESTAMP DEFAULT NOW()
);

-- 2. Inserir loja padrão (Matriz) para receber registros antigos
INSERT INTO lojas (cnpj, nome_fantasia, razao_social, endereco, municipio, uf, telefone)
VALUES ('00000000000000', 'Matriz', 'Pharmapele Matriz Ltda', 'Avenida Principal, 100 - Centro', 'Recife', 'PE', '')
ON CONFLICT (cnpj) DO NOTHING;

-- 3. Adicionar coluna loja_id em notas_fiscais e associar à loja padrão
ALTER TABLE notas_fiscais ADD COLUMN IF NOT EXISTS loja_id INTEGER REFERENCES lojas(id);
UPDATE notas_fiscais SET loja_id = (SELECT id FROM lojas WHERE cnpj = '00000000000000') WHERE loja_id IS NULL;

-- 4. Adicionar coluna loja_id em produtos, remover unicidade de código global e criar unicidade por loja
ALTER TABLE produtos DROP CONSTRAINT IF EXISTS produtos_codigo_key;
ALTER TABLE produtos ADD COLUMN IF NOT EXISTS loja_id INTEGER REFERENCES lojas(id);
UPDATE produtos SET loja_id = (SELECT id FROM lojas WHERE cnpj = '00000000000000') WHERE loja_id IS NULL;

ALTER TABLE produtos DROP CONSTRAINT IF EXISTS unique_codigo_loja;
ALTER TABLE produtos ADD CONSTRAINT unique_codigo_loja UNIQUE (codigo, loja_id);

-- 5. Adicionar coluna loja_id em alertas_recompra e criar unicidade por loja
ALTER TABLE alertas_recompra DROP CONSTRAINT IF EXISTS alertas_recompra_cliente_id_produto_id_key;
ALTER TABLE alertas_recompra ADD COLUMN IF NOT EXISTS loja_id INTEGER REFERENCES lojas(id);
UPDATE alertas_recompra SET loja_id = (SELECT id FROM lojas WHERE cnpj = '00000000000000') WHERE loja_id IS NULL;

ALTER TABLE alertas_recompra DROP CONSTRAINT IF EXISTS unique_cliente_produto_loja;
ALTER TABLE alertas_recompra ADD CONSTRAINT unique_cliente_produto_loja UNIQUE (cliente_id, produto_id, loja_id);

-- 6. Índices para performance
CREATE INDEX IF NOT EXISTS idx_nf_loja ON notas_fiscais(loja_id);
CREATE INDEX IF NOT EXISTS idx_prod_loja ON produtos(loja_id);
CREATE INDEX IF NOT EXISTS idx_alertas_loja ON alertas_recompra(loja_id);

-- 7. Recriar Views com suporte a loja_id e LEFT JOIN para clientes anônimos
DROP VIEW IF EXISTS vw_clientes_inativos CASCADE;
DROP VIEW IF EXISTS vw_frequencia_recompra CASCADE;
DROP VIEW IF EXISTS vw_vendas_detalhadas CASCADE;

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

COMMENT ON TABLE lojas IS 'Lojas cadastradas a partir do emitente da NFC-e';
