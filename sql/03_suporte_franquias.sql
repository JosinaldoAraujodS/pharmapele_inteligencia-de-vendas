-- =====================================================
-- PHARMAPELE - Script de Migração: Suporte a Franquias e Autenticação
-- Execute no pgAdmin ou psql conectado ao banco pharmapele
-- =====================================================

-- 1. Criar tabela: franquias
CREATE TABLE IF NOT EXISTS franquias (
    id SERIAL PRIMARY KEY,
    nome VARCHAR(255) NOT NULL,
    ativo BOOLEAN DEFAULT TRUE,
    criado_em TIMESTAMP DEFAULT NOW(),
    atualizado_em TIMESTAMP DEFAULT NOW()
);

-- 2. Inserir Franquia padrão (Franquia Matriz)
INSERT INTO franquias (id, nome, ativo)
VALUES (1, 'Franquia Matriz', TRUE)
ON CONFLICT (id) DO NOTHING;

-- Garantir que a sequência SERIAL de franquias comece depois de 1
SELECT setval(pg_get_serial_sequence('franquias', 'id'), COALESCE(MAX(id), 1)) FROM franquias;

-- 3. Adicionar coluna franquia_id na tabela lojas
ALTER TABLE lojas ADD COLUMN IF NOT EXISTS franquia_id INTEGER REFERENCES franquias(id);

-- 4. Associar todas as lojas existentes à franquia padrão
UPDATE lojas SET franquia_id = 1 WHERE franquia_id IS NULL;

-- 5. Criar tabela: usuarios para login e controle de acessos
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

-- 6. Inserir usuário Administrador Master padrão
-- Email: admin@pharmapele.com.br
-- Senha em texto plano: Ojuara10*
-- Hash bcrypt gerado de forma segura
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

-- 7. Índices para performance
CREATE INDEX IF NOT EXISTS idx_lojas_franquia ON lojas(franquia_id);
CREATE INDEX IF NOT EXISTS idx_usuarios_email ON usuarios(email);
CREATE INDEX IF NOT EXISTS idx_usuarios_franquia ON usuarios(franquia_id);
CREATE INDEX IF NOT EXISTS idx_usuarios_loja ON usuarios(loja_id);

COMMENT ON TABLE franquias IS 'Franquias registradas que gerenciam uma ou mais lojas';
COMMENT ON TABLE usuarios IS 'Usuários com acesso ao sistema segregados por nível de acesso';
