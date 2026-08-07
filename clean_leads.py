"""
Script para limpar e filtrar a lista de leads.
Mantém apenas clínicas e hospitais.
"""
import csv
import re

def parse_leads(filepath):
    """Parse the messy CSV format with escaped quotes."""
    leads = []

    with open(filepath, 'r', encoding='utf-8-sig') as f:  # utf-8-sig handles BOM
        content = f.read()

    lines = content.strip().split('\n')

    for line in lines[1:]:  # Skip header
        line = line.strip()
        if not line:
            continue

        # Format: "NAME,""ADDRESS"",email,phone,MANAGER"
        # Remove outer quotes first
        if line.startswith('"') and line.endswith('"'):
            line = line[1:-1]

        # Split by ,"" and "", to get: NAME | ADDRESS | rest
        parts = re.split(r',""', line, maxsplit=1)
        if len(parts) == 2:
            nome = parts[0]
            rest = parts[1]

            # Find the closing ""
            addr_match = re.match(r'^(.+?)"",(.+)$', rest)
            if addr_match:
                endereco = addr_match.group(1)
                remaining = addr_match.group(2)

                # Split remaining: email,phone,manager
                rem_parts = remaining.split(',', 2)
                if len(rem_parts) >= 3:
                    email = rem_parts[0].strip()
                    telefone = rem_parts[1].strip()
                    gestor = rem_parts[2].strip()

                    leads.append({
                        'nome_fantasia': nome.strip(),
                        'endereco': endereco.strip(),
                        'email': email if email != 'N/A' else '',
                        'telefone': telefone if telefone != 'N/A' else '',
                        'gestor': gestor.strip()
                    })

    return leads


def filter_clinics_hospitals(leads):
    """Filter only clinics and hospitals."""
    keywords = [
        'HOSPITAL', 'CLINICA', 'CLÍNICA', 'HOSP', 'CENTRO DE',
        'MEDICINA', 'MEDICA', 'MÉDICA', 'PROGASTRO', 'REUMAT',
        'NEURO', 'CARDIO', 'FISIO', 'INTEGRALLE', 'UNICENTRO'
    ]

    # Exclude keywords (pharmacies, labs, dental only, etc.)
    exclude = ['FARMACIA', 'DROGASIL', 'PAGUE MENOS', 'ODONTO', 'ORTODONTIC', 'PRO ORAL']

    filtered = []
    for lead in leads:
        nome_upper = lead['nome_fantasia'].upper()

        # Check if it matches include keywords
        matches_include = any(kw in nome_upper for kw in keywords)

        # Check if it matches exclude keywords
        matches_exclude = any(kw in nome_upper for kw in exclude)

        if matches_include and not matches_exclude:
            filtered.append(lead)

    return filtered


def save_clean_csv(leads, output_path):
    """Save leads to a clean CSV."""
    with open(output_path, 'w', encoding='utf-8', newline='') as f:
        writer = csv.DictWriter(f, fieldnames=['nome_fantasia', 'endereco', 'email', 'telefone', 'gestor'])
        writer.writeheader()
        writer.writerows(leads)


if __name__ == '__main__':
    # Parse original file
    leads = parse_leads('lista_leads.csv')
    print(f"Total leads lidos: {len(leads)}")

    # Filter clinics and hospitals
    filtered = filter_clinics_hospitals(leads)
    print(f"Clínicas/Hospitais filtrados: {len(filtered)}")

    # Save clean CSV
    save_clean_csv(filtered, 'leads_clinicas_hospitais.csv')
    print(f"\nArquivo salvo: leads_clinicas_hospitais.csv")

    # Print results
    print("\n" + "="*60)
    print("CLÍNICAS E HOSPITAIS ENCONTRADOS:")
    print("="*60)
    for i, lead in enumerate(filtered, 1):
        print(f"\n{i}. {lead['nome_fantasia']}")
        print(f"   📍 {lead['endereco'][:60]}...")
        print(f"   📧 {lead['email'] or 'N/A'}")
        print(f"   📞 {lead['telefone']}")
        print(f"   👤 {lead['gestor']}")
