# Pending Sites From `known_sites.json`

Compared:
- `gov_aggregator/data/known_sites.json`
- `gov_aggregator/data/sites_config.json`

Rule used:
- A site is treated as pending when its `site_key` from `known_sites.json` does not exist in `sites_config.json`.
- I also flagged rows that appear to already map to an existing configured scraper by matching host/domain.

Summary:
- Inventory rows in `known_sites.json`: 183
- Configured scraper entries in `sites_config.json`: 78
- Raw pending rows by `site_key`: 113
- Likely alias/already-covered rows: 9
- True pending backlog after alias review: 104
- True pending with `working` status: 62
- True pending with `not_working` status: 12
- True pending with `unknown` status: 30

## Alias Or Already Covered

These showed up as pending by `site_key`, but their domain already matches an existing configured scraper. Review these before starting manual scraping so we do not duplicate work.

- Ministry of Finance- Bifurcation (Gazette) | `ministry-of-finance-bifurcation-gazette` | unknown | matches `income-tax`
- Ministry of Finance- Bifurcation (Gazette) RBI | `ministry-of-finance-bifurcation-gazette-rbi` | unknown | matches `rbi`
- Ministry of Production (Gazette) | `ministry-of-production-gazette` | unknown | matches `minisry-of-commerce`
- CDSCO | `cdsco` | working | matches `central-drugs-standard-control-organisation`
- Department of Heavy Industries | `department-of-heavy-industries` | working | matches `ministry-of-heavy-industries`
- DGFT | `dgft` | working | matches `directorate-general-of-foreign-trade`
- Directorate of Cashewnut and Cocoa Development (DCCD) | `directorate-of-cashewnut-and-cocoa-development` | working | matches `directorate-of-cashewnut-cocoa-development`
- EPFO | `epfo` | working | matches `employees-provident-fund-organisation`
- Fire Service, Civil Defence | `fire-service-civil-defence` | working | matches `directorate-general-fire-services-civil-defence-home-guards`

## Working Backlog

These are the best manual scraping candidates because they are marked `working` and do not appear to already map to an existing configured scraper.

- Aadhaar | `aadhaar` | https://uidai.gov.in/
- Andhra Pradesh | `andhra-pradesh` | https://apegazette.cgg.gov.in/login.do
- Archaeological Survey of India | `archaeological-survey-of-india` | http://asiegov.gov.in/
- Arunachal Pradesh | `arunachal-pradesh` | http://www.arunachalpradesh.gov.in/gazette/
- BSE | `bse` | https://www.bseindia.com/
- CERC | `cerc` | https://cercind.gov.in/
- Chemexcil | `chemexcil` | https://chemexcil.in/
- Civil Aviation | `civil-aviation` | https://www.civilaviation.gov.in/
- Directorate of Plant Protection Quarantine & Storage (For Plant Quarantine Rules, Insectides Rules) | `directorate-of-plant-protection-quarantine-storage-for-plant-quarantine-rules-insectides-rules` | http://ppqs.gov.in/
- Falta SEZ | `falta-sez` | http://fsez.gov.in
- Goa | `goa` | https://goaprintingpress.gov.in/search-by-date/
- Gujarat | `gujarat` | https://dgps.gujarat.gov.in/webcontroller/postone/E-Gazettes
- Haryana | `haryana` | http://www.egazetteharyana.gov.in/
- Himachal Pradesh | `himachal-pradesh` | https://rajpatrahimachal.nic.in/Default.aspx
- Industry and Internal Trade_CIPAM_IP | `industry-and-internal-trade-cipam-ip` | http://cipam.gov.in
- Industry and Internal Trade_Economic Adv (Policies) | `industry-and-internal-trade-economic-adv-policies` | https://eaindustry.nic.in/
- Kandla SEZ | `kandla-sez` | http://kasez.gov.in/
- Karnataka | `karnataka` | https://erajyapatra.karnataka.gov.in/
- Kerala | `kerala` | http://www.egazette.kerala.gov.in/latest.php?id=2
- Maharashtra | `maharashtra` | https://egazzete.mahaonline.gov.in/Forms/GazetteSearch.aspx
- Manipur | `manipur` | https://manipurgovtpress.nic.in/index.php?option=com_gazette&task=+&Itemid=88
- MCA | `mca` | https://www.mca.gov.in/
- Meghalaya | `meghalaya` | http://megpns.gov.in/gazette/archive.asp
- MeiTY | `meity` | https://www.meity.gov.in/divisions
- Minisry of Labour | `minisry-of-labour` | https://labour.gov.in
- Ministry of Jal Sakthi (Water Resources) | `ministry-of-jal-sakthi-water-resources` | http://jalshakti-dowr.gov.in/
- Ministry of Personnel, Public Grievances & Pensions (Corruption Related) | `ministry-of-personnel-public-grievances-pensions-corruption-related` | https://persmin.gov.in/
- Ministry of Petroleum and Natural Gas | `ministry-of-petroleum-and-natural-gas` | https://mopng.gov.in/en
- Ministry of Power | `ministry-of-power` | https://powermin.gov.in/
- Ministry of Road Transport and Highways | `ministry-of-road-transport-and-highways` | https://morth.gov.in/index.php
- Mission for Integrated Development of Horticulture (Schemes & Guidelines) | `mission-for-integrated-development-of-horticulture-schemes-guidelines` | https://midh.gov.in/Letters&Circulars
- Mizoram | `mizoram` | https://printingstationery.mizoram.gov.in/gazettes
- Nagaland | `nagaland` | https://govtpress.nagaland.gov.in/egazette/
- National Agricultural Market ( APMC and Agricultural Warehousing Related Notifications) | `national-agricultural-market-apmc-and-agricultural-warehousing-related-notifications` | https://enam.gov.in/
- National Bea Board | `national-bea-board` | https://nbb.gov.in/
- National Centre for Cold-chain Development | `national-centre-for-cold-chain-development` | https://www.nccd.gov.in
- National Horticulture Board | `national-horticulture-board` | https://nfsm.gov.in/https://nfsm.gov.in/
- National Payments Corporation of India | `national-payments-corporation-of-india` | https://www.npci.org.in/
- Noida SEZ | `noida-sez` | https://www.nsez.gov.in/
- NSE | `nse` | https://www.nseindia.com/
- Odisha | `odisha` | https://govtpress.odisha.gov.in/gazdatewise.htm
- Pharmexcil | `pharmexcil` | https://pharmexcil.com/
- Puducherry | `puducherry` | https://styandptg.py.gov.in/2020/exordinary1jul20.html
- Punjab | `punjab` | https://punjab.gov.in/notifications/
- Rubber Board | `rubber-board` | http://rubberboard.gov.in/
- Saral Sanchar | `saral-sanchar` | https://saralsanchar.gov.in
- SEBI | `sebi` | https://www.sebi.gov.in/
- SEEPZ SEZ | `seepz-sez` | http://seepz.gov.in
- Services Export Promotion Council | `services-export-promotion-council` | www.servicesepc.org/
- SEZ | `sez` | http://sezindia.nic.in
- SFAC (Farmer Producer Organisation) | `sfac-farmer-producer-organisation` | http://sfacindia.com/
- Sikkim | `sikkim` | https://sikkim.gov.in/mygovernment/gazettes
- Spices Board | `spices-board` | www.indianspices.com
- State GST | `state-gst` | https://gstcouncil.gov.in/sgst-tax-notifications
- Tamil Nadu | `tamil-nadu` | http://www.stationeryprinting.tn.gov.in/extraordinary/extraord_list.php
- Tea Board | `tea-board` | http://www.teaboard.gov.in
- Trade Portal | `trade-portal` | https://www.indiantradeportal.in/
- Tripura | `tripura` | https://egazette.tripura.gov.in/eGazette/
- Uttar Pradesh | `uttar-pradesh` | www.upvidhansabhaproceedings.gov.in
- Uttarakhand | `uttarakhand` | http://gazettes.uk.gov.in/
- Vizag SEZ | `vizag-sez` | http://www.vsez.gov.in/
- West Bengal | `west-bengal` | https://wb.gov.in/documents-notification.aspx

## Not Working Backlog

These are still pending, but the inventory marks them as `not_working`, so expect more time for debugging or alternate source discovery.

- Bihar | `bihar` | https://egazette.bihar.gov.in/
- CBIC (Customs & Exices) | `cbic-customs-exices` | https://www.cbic.gov.in/entities/customs
- Chemicals and Fertilizers | `chemicals-and-fertilizers` | https://www.mocf.gov.in/
- Coffee Board | `coffee-board` | https://coffeeboard.gov.in/
- Communications | `communications` | https://dot.gov.in/
- Department of Animal Husbandary and Dairying | `department-of-animal-husbandary-and-dairying` | https://www.dahd.gov.in/
- Directorate of Arecanut and Spices Development (Spices) | `directorate-of-arecanut-and-spices-development-spices` | https://spicenurseries.in/
- ICSI | `icsi` | https://icsi.edu/home/
- Jharkhand | `jharkhand` | https://egazette.jharkhand.gov.in/
- Madhya Pradesh | `madhya-pradesh` | https://pam.mp.gov.in/gadget-notification
- National Capital Territory of Delhi | `national-capital-territory-of-delhi` | https://delhi.gov.in/centralized-cos
- Plant Protection, Quarantie and Storage (Import and Export related) | `plant-protection-quarantie-and-storage-import-and-export-related` | https://ppqs.gov.in/en

## Unknown Or Inventory Cleanup

These rows still need cleanup or source confirmation before manual scraping. Most have `N/A`, malformed URLs, or status text stored in URL fields.

- Andaman ana Nicobar Islands | `andaman-ana-nicobar-islands` | N/A
- Assam | `assam` | N/A
- Chandigarh | `chandigarh` | N/A
- Chattisgarh | `chattisgarh` | N/A
- Dadra and Nagar Haveli and Daman and Diu | `dadra-and-nagar-haveli-and-daman-and-diu` | N/A
- https://cercind.gov.in/JERC-Regulation/jerc-home.htm | `https-cercind-gov-in-jerc-regulation-jerc-home-htm` | N/A
- https://delhi.gov.in/notice-board/notifications | `https-delhi-gov-in-notice-board-notifications` | N/A
- https://electricity.py.gov.in/regulations-jerc | `https-electricity-py-gov-in-regulations-jerc` | N/A
- https://finance.mn.gov.in/index.aspx | `https-finance-mn-gov-in-index-aspx` | working
- https://govtpress.mp.gov.in/gazette | `https-govtpress-mp-gov-in-gazette` | N/A
- https://jerc.mizoram.gov.in/ | `https-jerc-mizoram-gov-in` | working
- https://shramevjayate.cg.gov.in/ | `https-shramevjayate-cg-gov-in` | N/A
- https://tggazette.cgg.gov.in/ | `https-tggazette-cgg-gov-in` | N/A
- https://www.indianemployees.com/gazette-notifications/department/mp-home/ | `https-www-indianemployees-com-gazette-notifications-department-mp-home` | N/A
- Insolvency and Bankruptcy Board of India (Gazette) | `insolvency-and-bankruptcy-board-of-india-gazette` | N/A
- Jammu and kashmir | `jammu-and-kashmir` | N/A
- Joint Electricity Regulatory Commission for Union Territories of Jammu and kashmir and Union Territories of Ladakh (Gazette) | `joint-electricity-regulatory-commission-for-union-territories-of-jammu-and-kashmir-and-union-territories-of-ladakh-gazette` | N/A
- Ladakh | `ladakh` | N/A
- Lakshwadeep | `lakshwadeep` | N/A
- Ministry of Cooperation (Gazette) | `ministry-of-cooperation-gazette` | N/A
- Ministry of External Affairs (Gazette) | `ministry-of-external-affairs-gazette` | N/A
- Ministry of Finance- Bifurcation (Gazette) | `ministry-of-finance-bifurcation-gazette` | N/A
- Ministry of Micro, Small and Medium Enterprises (Gazette) | `ministry-of-micro-small-and-medium-enterprises-gazette` | N/A
- Ministry of Overseas Indian Affairs (Gazette) | `ministry-of-overseas-indian-affairs-gazette` | N/A
- Ministry of Small Scale Industries (Gazette) | `ministry-of-small-scale-industries-gazette` | N/A
- N/A | `n-a` | https://www.cbic.gov.in/
- Rajasthan | `rajasthan` | not working
- reams.rajasthan.gov.in/PrintingStationary/GuestSearchOrdinaryCitizen | `reams-rajasthan-gov-in-printingstationary-guestsearchordinarycitizen` | N/A
- Telangana | `telangana` | not working
- The Competition Commission of India (Gazette) | `the-competition-commission-of-india-gazette` | N/A
