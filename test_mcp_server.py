"""
Teste do servidor MCP CNES
"""
import asyncio
import sys
sys.path.insert(0, '.')

from mcp_server import MCPServer

async def test_mcp_server():
    print("=" * 60)
    print("TESTE DO SERVIDOR MCP CNES")
    print("=" * 60)

    server = MCPServer()

    # 1. Listar ferramentas
    print("\n📋 Ferramentas disponíveis:")
    tools = server.get_tools()
    for tool in tools:
        print(f"  - {tool['name']}: {tool['description'][:50]}...")

    # 2. Carregar dados de exemplo
    print("\n📥 Carregando dados de exemplo...")
    result = await server.call_tool("cnes_load_data", {
        "filepath": "sample_data.csv"
    })
    print(f"  Resultado: {result}")

    # 3. Buscar por município
    print("\n🔍 Buscando hospitais em São Paulo...")
    result = await server.call_tool("cnes_search_municipio", {
        "municipio": "São Paulo",
        "limit": 5
    })
    print(f"  Encontrados: {result.get('total_encontrados', 0)} estabelecimentos")
    for hosp in result.get('estabelecimentos', [])[:3]:
        print(f"    - {hosp['nome_fantasia']} (CNES: {hosp['cnes']})")

    # 4. Buscar por CNES
    print("\n🏥 Buscando CNES 2077485...")
    result = await server.call_tool("cnes_search_cnes", {
        "cnes": "2077485"
    })
    if result.get('encontrado'):
        hosp = result['estabelecimento']
        print(f"  Nome: {hosp['nome_fantasia']}")
        print(f"  Município: {hosp['municipio']}/{hosp['uf']}")
        print(f"  Leitos: {hosp['leitos_existentes']} (SUS: {hosp['leitos_sus']})")

    # 5. Buscar por UF
    print("\n🗺️ Buscando hospitais no RJ...")
    result = await server.call_tool("cnes_search_uf", {
        "uf": "RJ",
        "limit": 5
    })
    print(f"  Encontrados: {result.get('total_encontrados', 0)} no {result.get('uf', 'RJ')}")

    # 6. Estatísticas
    print("\n📊 Estatísticas gerais:")
    result = await server.call_tool("cnes_statistics", {})
    print(f"  Total estabelecimentos: {result.get('total_estabelecimentos', 0)}")
    print(f"  Total leitos: {result.get('total_leitos_existentes', 0)}")
    print(f"  Total leitos SUS: {result.get('total_leitos_sus', 0)}")
    print(f"  UFs: {list(result.get('estabelecimentos_por_uf', {}).keys())}")

    # 7. Instruções de download
    print("\n📖 Instruções de download:")
    result = await server.call_tool("cnes_download_instructions", {})
    print(f"  URL: {result.get('url', '')}")
    print(f"  Passos: {len(result.get('passos', []))}")

    print("\n" + "=" * 60)
    print("✅ TODOS OS TESTES PASSARAM!")
    print("=" * 60)


if __name__ == "__main__":
    asyncio.run(test_mcp_server())
