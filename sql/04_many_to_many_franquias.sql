-- =====================================================
-- PHARMAPELE - Script de Migração: Relacionamento Muitos-para-Muitos (Franquias e Usuários)
-- Execute no pgAdmin ou psql conectado ao banco pharmapele
-- =====================================================

-- 1. Criar tabela de junção muitos-para-muitos
CREATE TABLE IF NOT EXISTS usuario_franquias (
    usuario_id INTEGER REFERENCES usuarios(id) ON DELETE CASCADE,
    franquia_id INTEGER REFERENCES franquias(id) ON DELETE CASCADE,
    criado_em TIMESTAMP DEFAULT NOW(),
    PRIMARY KEY (usuario_id, franquia_id)
);

-- 2. Migrar associações existentes da tabela usuarios (onde franquia_id não é nulo)
INSERT INTO usuario_franquias (usuario_id, franquia_id)
SELECT id, franquia_id 
FROM usuarios 
WHERE franquia_id IS NOT NULL
ON CONFLICT (usuario_id, franquia_id) DO NOTHING;

-- 3. Índices para otimização de consultas
CREATE INDEX IF NOT EXISTS idx_usuario_franquias_usuario ON usuario_franquias(usuario_id);
CREATE INDEX IF NOT EXISTS idx_usuario_franquias_franquia ON usuario_franquias(franquia_id);

COMMENT ON TABLE usuario_franquias IS 'Tabela de junção muitos-para-muitos associando usuários do tipo franqueado a uma ou mais franquias';
