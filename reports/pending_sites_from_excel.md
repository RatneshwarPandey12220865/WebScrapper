# Pending Sites From Final URL Excel

Compared:
- `C:\Users\pande\Downloads\Final_URL_5.02.2026 (2).xlsx`
- `gov_aggregator/data/sites_config.json`

Rule used:
- A site is treated as "done" only if it already has a matching crawler config in `sites_config.json`, matched by normalized name or normalized host/domain.
- Blank-name continuation rows in the Excel sheet were ignored as standalone sites.
- Host matching strips a leading `www.` so the Excel sheet and config can be compared more accurately.

Summary:
- Data rows in Excel: 178
- Blank-name continuation rows ignored: 11
- Named website rows used for comparison: 167
- Already configured: 65
- Not yet configured: 102
- Pending with `working` status: 71
- Pending with `not working` status: 13
- Pending with `N/A` status: 18

## Easy First Candidates

These look like the best starting points from the final workbook because they are marked `working` and appear likely to be normal HTML/static sites or straightforward index pages.

1. Advertising Standards Council of India
2. AYUSH
3. Consumer Affairs
4. Department of Bio Technology
5. Department of Pharmaceuticals
6. Pharmacy Council
7. CERC
8. Industry and Internal Trade_Economic Adv (Policies)
9. Industry and Internal Trade_Foreign Trade
10. National Centre for Cold-chain Development
11. National Agricultural Market ( APMC and Agricultural Warehousing Related Notifications)
12. Ministry of Food Processing Industries
13. Noida SEZ
14. Saral Sanchar
15. Trade Portal

Likely harder even though marked `working`:
- NSE
- BSE
- National Payments Corporation of India
- Aadhaar
- CBIC (Only GST)
- Gazette/search/login portals such as Andhra Pradesh, Maharashtra, Gujarat, Karnataka, Telangana, Rajasthan, Bihar, Jharkhand, Delhi, and other state gazette systems

## Full Pending List

### Working

- Aadhaar
- Advertising Standards Council of India
- Andhra Pradesh
- Archaeological Survey of India
- Arunachal Pradesh
- BSE
- CBIC (Only GST)
- CERC
- Chemexcil
- Civil Aviation
- Consumer Affairs
- Department of Bio Technology
- Department of Pharmaceuticals
- Dept of Empowerment of persons with disabilities
- Directorate of Plant Protection Quarantine & Storage (For Plant Quarantine Rules, Insectides Rules)
- Falta SEZ
- Goa
- GST
- Gujarat
- Haryana
- Himachal Pradesh
- Industry and Internal Trade_CIPAM_IP
- Industry and Internal Trade_Economic Adv (Policies)
- Industry and Internal Trade_Foreign Trade
- Kandla SEZ
- Karnataka
- Kerala
- Maharashtra
- Manipur
- MCA
- Meghalaya
- MeiTY
- Minisry of Labour
- Ministry Of Coal (For Coal Mines)
- Ministry of Food Processing Industries
- Ministry of Jal Sakthi (Water Resources)
- Ministry of Personnel, Public Grievances & Pensions (Corruption Related)
- Ministry of Petroleum and Natural Gas
- Ministry of Power
- Ministry of Road Transport and Highways
- Mission for Integrated Development of Horticulture (Schemes & Guidelines)
- Mizoram
- Nagaland
- National Agricultural Market ( APMC and Agricultural Warehousing Related Notifications)
- National Bea Board
- National Centre for Cold-chain Development
- National Payments Corporation of India
- Noida SEZ
- NSE
- Odisha
- Pharmacy Council
- Pharmexcil
- Puducherry
- Punjab
- Rubber Board
- Saral Sanchar
- SEEPZ SEZ
- Services Export Promotion Council
- SEZ
- SFAC (Farmer Producer Organisation)
- Sikkim
- Spices Board
- State GST
- Tamil Nadu
- Tea Board
- Trade Portal
- Tripura
- Uttar Pradesh
- Uttarakhand
- Vizag SEZ
- West Bengal

### Not Working

- Bihar
- CBIC (Customs & Exices)
- Chemicals and Fertilizers
- Coffee Board
- Department of Animal Husbandary and Dairying
- Directorate of Arecanut and Spices Development (Spices)
- ICSI
- Jharkhand
- Madhya Pradesh
- National Capital Territory of Delhi
- Plant Protection, Quarantie and Storage (Import and Export related)
- Rajasthan
- Telangana

### N/A

- Andaman ana Nicobar Islands
- Assam
- Chandigarh
- Chattisgarh
- Dadra and Nagar Haveli and Daman and Diu
- Insolvency and Bankruptcy Board of India (Gazette)
- Jammu and kashmir
- Joint Electricity Regulatory Commission for Goa and Union Territories (Gazette)
- Joint Electricity Regulatory Commission for Union Territories of Jammu and kashmir and Union Territories of Ladakh (Gazette)
- Ladakh
- Lakshwadeep
- Ministry of Cooperation (Gazette)
- Ministry of External Affairs (Gazette)
- Ministry of Finance- Bifurcation (Gazette)
- Ministry of Micro, Small and Medium Enterprises (Gazette)
- Ministry of Overseas Indian Affairs (Gazette)
- Ministry of Small Scale Industries (Gazette)
- The Competition Commission of India (Gazette)

## Blank Continuation Rows Ignored

These 11 rows in the workbook have no website name, so they were not counted as standalone sites. They may still be useful as alternate source pages for later manual review:

- `https://shramevjayate.cg.gov.in/`
- `https://electricity.py.gov.in/regulations-jerc`
- `https://cercind.gov.in/JERC-Regulation/jerc-home.htm`
- `https://tggazette.cgg.gov.in/`
- `https://www.cbic.gov.in/`
- `https://jerc.mizoram.gov.in/`
- `https://finance.mn.gov.in/index.aspx`
- `https://delhi.gov.in/notice-board/notifications`
- `https://govtpress.mp.gov.in/gazette`
- `https://www.indianemployees.com/gazette-notifications/department/mp-home/`
- `reams.rajasthan.gov.in/PrintingStationary/GuestSearchOrdinaryCitizen`
