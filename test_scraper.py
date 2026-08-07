"""
Script de teste para o scraper CNES.
Executa com apenas uma cidade para validar o funcionamento.
"""
import sys
sys.path.insert(0, '.')

from cnes_scraper import CNESScraper, CNESConfig

# Configuração de teste com apenas Manaus
test_config = CNESConfig()
test_config.TARGET_CITIES = {
    "NORTE": ["MANAUS"]
}

print("="*60)
print("TESTE DO SCRAPER CNES - APENAS MANAUS")
print("="*60)

# Criar scraper
scraper = CNESScraper(test_config)

# Testar uma cidade
hospitals = scraper.fetch_hospitals_by_city("MANAUS")

print(f"\nResultado: {len(hospitals)} hospitais encontrados")

if hospitals:
    print("\nPrimeiro hospital encontrado:")
    for key, value in hospitals[0].items():
        print(f"  {key}: {value}")
else:
    print("\nNenhum hospital encontrado. Verificando conectividade...")

    # Teste básico de conexão
    import requests
    try:
        r = requests.get("https://elasticnes.saude.gov.br/", timeout=10)
        print(f"Conexão OK - Status: {r.status_code}")
    except Exception as e:
        print(f"Erro de conexão: {e}")
