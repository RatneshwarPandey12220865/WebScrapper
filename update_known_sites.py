import json

with open('gov_aggregator/data/known_sites.json', 'r') as f:
    data = json.load(f)

not_working_keys = {
    'central-electricity-regulatory-commission',
    'cochin-sez',
    'directorate-of-marketing-and-inspection-for-pulses-grading-and-marking',
    'directorate-of-plant-protection-quarantine-storage-for-plant-quarantine-rules-insectides-rules',
    'falta-sez',
    'industry-and-internal-trade-ip',
    'jammu-and-kashmir',
    'madhya-pradesh',
    'ministry-of-food-processing-industries',
    'ministry-of-petroleum-and-natural-gas',
    'punjab',
    'vizag-sez',
}

working_keys = {
    'chattisgarh',
    'chandigarh',
    'lakshwadeep',
    'ministry-of-cooperation-gazette',
    'joint-electricity-regulatory-commission-for-union-territories-of-jammu-and-kashmir-and-union-territories-of-ladakh-gazette',
    'ministry-of-health-welfare',
    'jharkhand',
    'bihar',
    'national-capital-territory-of-delhi',
    'assam',
    'ladakh',
    'andaman-ana-nicobar-islands',
    'dadra-and-nagar-haveli-and-daman-and-diu',
    'ministry-of-micro-small-and-medium-enterprises-gazette',
    'ministry-of-overseas-indian-affairs-gazette',
    'ministry-of-small-scale-industries-gazette',
    'ministry-of-external-affairs-gazette',
    'insolvency-and-bankruptcy-board-of-india-gazette',
    'the-competition-commission-of-india-gazette',
    'rajasthan',
    'telangana',
    'department-of-agriculture-and-farmers-welfare',
    'department-of-animal-husbandary-and-dairying',
    'directorate-of-arecanut-and-spices-development-spices',
    'plant-protection-quarantie-and-storage-import-and-export-related',
    'chemicals-and-fertilizers',
    'communications',
    'ministry-of-textiles',
    'department-of-land-resources',
    'cbic-customs-exices',
}

preferred_url_updates = {
    'chattisgarh': 'https://egazette.cg.nic.in/',
    'chandigarh': 'https://egazette.chd.gov.in/',
    'lakshwadeep': 'https://lakshadweep.gov.in/document-category/gazatte-notifications/',
    'jammu-and-kashmir': 'https://egazette.jk.gov.in/',
    'assam': 'https://dpns.assam.gov.in/',
    'andaman-ana-nicobar-islands': 'https://northmiddle.andaman.nic.in/document/andaman-and-nicobar-gazette-notification/',
    'dadra-and-nagar-haveli-and-daman-and-diu': 'https://ddd.gov.in/document-category/official-gazette/',
    'rajasthan': 'https://reams.rajasthan.gov.in/PrintingStationary/GuestSearch',
    'telangana': 'https://www.telangana.gov.in/te/gazette/',
    'national-capital-territory-of-delhi': 'https://delhi.gov.in/centralized-cos',
    'ministry-of-cooperation-gazette': 'https://www.cooperation.gov.in/',
    'insolvency-and-bankruptcy-board-of-india-gazette': 'https://ibbi.gov.in/',
    'the-competition-commission-of-india-gazette': 'https://cci.gov.in/',
    'ministry-of-micro-small-and-medium-enterprises-gazette': 'https://msme.gov.in/',
    'ministry-of-external-affairs-gazette': 'https://www.mea.gov.in/',
    'ministry-of-overseas-indian-affairs-gazette': 'https://www.mea.gov.in/',
    'ministry-of-small-scale-industries-gazette': 'https://msme.gov.in/',
    'joint-electricity-regulatory-commission-for-union-territories-of-jammu-and-kashmir-and-union-territories-of-ladakh-gazette': 'https://jercjkl.jk.gov.in/',
    'ministry-of-health-welfare': 'https://www.mohfw.gov.in/?q=en',
    'bihar': 'https://egazette.bihar.gov.in/',
    'jharkhand': 'https://egazette.jharkhand.gov.in/',
    'department-of-agriculture-and-farmers-welfare': 'https://www.agriwelfare.gov.in/',
    'department-of-animal-husbandary-and-dairying': 'https://www.dahd.gov.in/',
    'plant-protection-quarantie-and-storage-import-and-export-related': 'https://ppqs.gov.in/en',
    'chemicals-and-fertilizers': 'https://www.mocf.gov.in/',
    'communications': 'https://dot.gov.in/',
    'ministry-of-textiles': 'https://www.texmin.gov.in/',
    'department-of-land-resources': 'https://dolr.gov.in',
    'cbic-customs-exices': 'https://www.cbic.gov.in/entities/customs',
    'directorate-of-arecanut-and-spices-development-spices': 'https://spicenurseries.in/',
    'ministry-of-petroleum-and-natural-gas': 'https://mopng.gov.in/en',
    'ministry-of-food-processing-industries': 'https://mofpi.nic.in/',
}

changes = []

for site in data['sites']:
    key = site['site_key']

    # Handle ministry-of-finance-bifurcation-gazette specially (two entries)
    if key == 'ministry-of-finance-bifurcation-gazette':
        alt = site.get('alternate_url', '') or ''
        if 'financialservices.gov.in' in alt:
            site['status'] = 'working'
            site['status_raw'] = 'working'
            site['preferred_url'] = 'https://financialservices.gov.in/beta/en'
            changes.append(f"  {key} (financialservices) -> working + preferred_url updated")
        else:
            # incometaxindia one - also set to working per rule 7
            site['status'] = 'working'
            site['status_raw'] = 'working'
            changes.append(f"  {key} (incometaxindia) -> working")
        continue

    if key in not_working_keys:
        site['status'] = 'not_working'
        site['status_raw'] = 'not_working'
        changes.append(f"  {key} -> not_working")
    elif key in working_keys:
        site['status'] = 'working'
        site['status_raw'] = 'working'
        changes.append(f"  {key} -> working")

    if key in preferred_url_updates:
        site['preferred_url'] = preferred_url_updates[key]
        changes.append(f"  {key} preferred_url -> {preferred_url_updates[key]}")

print("Changes applied:")
for c in changes:
    print(c)

with open('gov_aggregator/data/known_sites.json', 'w') as f:
    json.dump(data, f, indent=2)

print(f"\nTotal sites: {len(data['sites'])}")
print("File written successfully.")
