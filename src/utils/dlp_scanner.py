"""
Data Loss Prevention (DLP) Scanner
Comprehensive protection against personal data leakage

Blocks Bitcoin/Nostr secrets, financial data, personal information
"""

import re
import hashlib
import logging
from typing import List, Dict, Tuple, Optional, Set
from dataclasses import dataclass
from enum import Enum

logger = logging.getLogger(__name__)


class DataCategory(Enum):
    """Categories of sensitive data."""
    BITCOIN_SECRET = "bitcoin_secret"
    BITCOIN_ADDRESS = "bitcoin_address"
    NOSTR_SECRET = "nostr_secret"
    NOSTR_PUBLIC = "nostr_public"
    LIGHTNING = "lightning"
    FINANCIAL_ACCOUNT = "financial_account"
    CREDIT_CARD = "credit_card"
    PERSONAL_ADDRESS = "personal_address"
    PHONE_NUMBER = "phone_number"
    GPS_COORDINATES = "gps_coordinates"
    IP_ADDRESS = "ip_address"
    WIFI_CREDENTIAL = "wifi_credential"
    API_KEY = "api_key"
    PRIVATE_KEY = "private_key"
    SEED_PHRASE = "seed_phrase"
    SSN = "ssn"  # Social Security Number
    PASSPORT = "passport"
    EMAIL = "email"
    MAC_ADDRESS = "mac_address"


@dataclass
class DLPViolation:
    """Represents a detected sensitive data pattern."""
    category: DataCategory
    pattern_name: str
    matched_text: str
    position: Tuple[int, int]
    severity: str
    description: str
    redacted: str
    
    def __post_init__(self):
        # Hash the matched text for logging (don't log actual secrets)
        self.text_hash = hashlib.sha256(self.matched_text.encode()).hexdigest()[:16]


class DLPPatterns:
    """
    Comprehensive data patterns for DLP scanning.
    
    Covers:
    - Bitcoin: Seeds, private keys, xpub/zpub, addresses, WIF
    - Nostr: nsec, npub
    - Lightning: Macaroon, invoice, node IDs
    - Financial: Bank accounts, credit cards, IBAN
    - Personal: Addresses, phones, GPS, emails
    - Network: IPs, MACs, WiFi credentials
    - Secrets: API keys, private keys, seeds
    """
    
    # ========== BITCOIN PATTERNS ==========
    BITCOIN = {
        # BIP39 Seed Phrases (12, 15, 18, 21, 24 words)
        'bip39_seed_12': (
            r'\b(?:abandon|ability|able|about|above|absent|absorb|abstract|absurd|abuse|abuse|academy|accent|accept|access|accident|account|accuse|achieve|acid|acoustic|acquire|across|act|action|actor|actress|actual|adapt|add|addict|address|adjust|admit|adult|advance|advice|aerobic|affair|afford|afraid|again|age|agent|agree|ahead|aim|air|airport|aisle|alarm|album|alcohol|alert|alien|alike|alive|all|alley|allow|almost|alone|alpha|already|also|alter|always|amateur|amazing|among|amount|amused|analyst|anchor|ancient|anger|angle|angry|animal|ankle|announce|annual|another|answer|antenna|antique|anxiety|any|apart|apology|appear|apple|approve|april|arch|arctic|area|arena|argue|arm|armed|armor|army|around|arrange|arrest|arrive|arrow|art|artefact|artist|artwork|ask|aspect|assault|asset|assist|assume|asthma|athlete|atom|attack|attend|attitude|attract|auction|audit|august|aunt|author|auto|autumn|average|avocado|avoid|awake|aware|away|awesome|awful|awkward|axis|baby|bachelor|bacon|badge|bag|balance|balcony|ball|bamboo|banana|banner|bar|barely|bargain|barrel|base|basic|basket|battle|beach|bean|beauty|because|become|beef|before|begin|behave|behind|believe|below|belt|bench|benefit|best|betray|better|between|beyond|bicycle|bid|bike|bind|biology|bird|birth|bitter|black|blade|blame|blanket|blast|bleak|bless|blind|blood|blossom|blouse|blue|blur|blush|board|boat|body|boil|bomb|bone|bonus|book|boost|border|boring|borrow|boss|bottom|bounce|box|boy|bracket|brain|brake|branch|brand|brass|brave|bread|breeze|brick|bridge|brief|bright|brilliant|bring|broccoli|broken|bronze|broom|brother|brown|brush|bubble|buddy|budget|buffalo|build|bulb|bulk|bullet|bundle|bunker|burden|burger|burst|bus|business|busy|butter|buyer|buzz|cabbage|cabin|cable|cactus|cage|cake|call|calm|camera|camp|can|canal|cancel|candy|cannon|canoe|canvas|canyon|capable|capital|captain|car|carbon|card|cargo|carpet|carry|cart|case|cash|casino|castle|casual|cat|catalog|catch|category|cattle|caught|cause|caution|cave|ceiling|celery|cement|census|center|century|cereal|certain|chair|chalk|champion|change|chaos|chapter|charge|chase|chat|cheap|check|cheese|chef|cherry|chest|chicken|chief|child|chimney|choice|choose|chronic|chuckle|chunk|churn|cigar|cinnamon|circle|citizen|city|civil|claim|clap|clarify|claw|clay|clean|clerk|clever|click|client|cliff|climb|clinic|clip|clock|clog|close|cloth|cloud|clown|club|clump|cluster|clutch|coach|coast|coconut|code|coffee|coil|coin|collect|color|column|combine|come|comfort|comic|common|company|concert|conduct|confirm|congress|connect|consider|control|cook|cool|copper|copy|coral|core|corn|correct|cost|cotton|couch|country|couple|course|cousin|cover|coyote|crack|cradle|craft|cram|crane|crash|crater|crawl|crazy|cream|credit|creek|crew|cricket|crime|crisp|critic|crop|cross|crouch|crowd|crucial|cruel|cruise|crumble|crunch|crush|cry|crystal|cube|culture|cup|cupboard|curious|current|curtain|curve|cushion|custom|cute|cycle|dad|damage|damp|dance|danger|daring|dash|daughter|dawn|day|deal|debate|debris|decade|december|decide|decline|decorate|decrease|deer|defense|define|defy|degree|delay|deliver|demand|demise|denial|dentist|deny|depart|depend|deposit|depth|deputy|derive|describe|desert|design|desk|despair|destroy|detail|detect|develop|device|devote|diagram|dial|diamond|diary|dice|diesel|diet|differ|digital|dignity|dilemma|dinner|dinosaur|direct|dirt|disagree|discover|disease|dish|dismiss|disorder|display|distance|divert|divide|divorce|dizzy|doctor|document|dog|doll|dolphin|domain|donate|donkey|donor|door|dose|double|dove|draft|dragon|drama|drastic|draw|dream|dress|drift|drill|drink|drip|drive|drop|drum|dry|duck|dumb|dune|during|dust|dutch|duty|dwarf|dynamic|eager|eagle|early|earn|earth|easily|east|easy|echo|ecology|economy|edge|edit|educate|effort|egg|eight|either|elbow|elder|electric|elegant|element|elephant|elevator|elite|else|embark|embody|embrace|emerge|emotion|employ|empty|enable|enact|end|endless|endorse|enemy|energy|enforce|engage|engine|enhance|enjoy|enlist|enough|enrich|enroll|ensure|enter|entire|entry|envelope|episode|equal|equip|era|erase|erode|erosion|error|erupt|escape|essay|essence|estate|eternal|ethics|evidence|evil|evoke|evolve|exact|example|excess|exchange|excite|exclude|excuse|execute|exercise|exhaust|exhibit|exile|exist|exit|exotic|expand|expect|expire|explain|explode|explore|export|expose|express|extend|extra|eye|eyebrow|fabric|face|faculty|fade|faint|faith|fall|false|fame|family|famous|fan|fancy|fantasy|farm|fashion|fat|fatal|father|fatigue|fault|favorite|feature|february|federal|fee|feed|feel|female|fence|festival|fetch|fever|few|fiber|fiction|field|fierce|fiery|fifth|fight|figure|file|film|filter|final|find|fine|finger|finish|fire|firm|first|fiscal|fish|fit|fitness|fix|flag|flame|flash|flat|flavor|flee|flight|flip|float|flock|floor|flower|fluid|flush|flute|fly|foam|focus|fog|foil|fold|follow|food|foot|force|forest|forget|fork|fortune|forum|forward|fossil|foster|found|fox|fragile|frame|frequent|fresh|friend|fringe|frog|front|frost|frown|frozen|fruit|fuel|fun|funny|furnace|fury|future|gadget|gain|galaxy|gallery|game|gap|garage|garbage|garden|garlic|garment|gas|gasp|gate|gather|gauge|gaze|general|genius|genre|gentle|genuine|gesture|ghost|giant|gift|giggle|ginger|giraffe|girl|give|glad|glance|glare|glass|glide|glimpse|globe|gloom|glory|glove|glow|glue|goat|goddess|gold|good|goose|gospel|gossip|govern|gown|grab|grace|grade|gradual|grain|grand|grant|grape|grass|gravity|great|green|grid|grief|grit|grocery|group|grow|grunt|guard|guess|guide|guilt|guitar|gun|gym|habit|hair|half|hammer|hamster|hand|happy|harbor|hard|harsh|harvest|hat|have|hawk|hazard|head|health|heart|heavy|hedgehog|height|hello|help|hen|hero|hidden|high|hill|hint|hip|hire|history|hobby|hockey|hold|hole|holiday|hollow|holy|home|honey|hood|hope|horn|horror|horse|hospital|host|hotel|hour|hover|hub|huge|human|humble|humor|hundred|hungry|hunt|hurdle|hurry|hurt|husband|hybrid|ice|icon|idea|identify|idle|ignore|ill|illegal|illness|image|imitate|immense|immune|impact|impose|improve|impulse|inch|include|income|increase|index|indicate|indoor|industry|infant|inflict|inform|inhale|inject|injury|inmate|inner|innocent|input|inquiry|insane|insect|inside|inspire|install|intact|interest|into|invest|invite|involve|iron|island|isolate|issue|item|ivory|jacket|jaguar|jar|jazz|jealous|jeans|jelly|jewel|job|join|joke|journey|joy|judge|juice|jump|jungle|junior|junk|just|kangaroo|keen|keep|ketchup|key|kick|kid|kidney|kind|kingdom|kiss|kit|kitchen|kite|kitten|kiwi|knee|knife|knock|know|lab|label|labor|ladder|lady|lake|lamp|language|laptop|large|later|latin|laugh|laundry|lava|law|lawn|lawsuit|layer|lazy|leader|leaf|learn|leave|lecture|left|leg|legal|legend|leisure|lemon|lend|length|lens|leopard|lesson|letter|level|liar|liberty|library|license|life|lift|light|like|limb|limit|link|lion|liquid|list|little|live|lizard|load|loan|lobster|local|lock|logic|lonely|long|loop|lottery|loud|lounge|love|loyal|lucky|luggage|lumber|lunar|lunch|luxury|lyrics|machine|mad|magic|magnet|maid|mail|main|major|make|mammal|man|manage|mandate|mango|mansion|manual|maple|marble|march|margin|marine|market|marriage|mask|mass|master|match|material|math|matrix|matter|maximum|maze|meadow|mean|measure|meat|mechanic|medal|media|melody|melt|member|memory|mention|menu|mercy|merge|merit|merry|message|metal|method|middle|midnight|milk|million|mimic|mind|minimum|minister|minor|minute|miracle|mirror|misery|miss|mistake|mix|mixed|mixture|moan|model|modify|mom|moment|monitor|monkey|month|moon|moral|more|morning|mosquito|mother|motion|motor|mountain|mouse|move|movie|much|muffin|mule|multiply|muscle|museum|mushroom|music|must|mutual|myself|mystery|myth|naive|name|napkin|narrow|nasty|nation|nature|near|neck|need|negative|neglect|neither|nephew|nerve|nest|net|network|neutral|never|news|next|nice|niece|night|nine|noble|noise|nominee|noodle|normal|north|nose|notable|note|nothing|notice|novel|now|nuclear|number|nurse|nut|oak|obey|object|oblige|obscure|observe|obtain|obvious|occur|ocean|october|odor|off|offer|office|often|oil|okay|old|olive|olympic|omit|once|one|onion|online|only|open|opera|opinion|opponent|option|orange|orbit|orchard|order|ordinary|organ|orient|original|orphan|ostrich|other|outer|outfit|oval|oven|over|own|owner|oxygen|oyster|ozone|pact|paddle|page|pair|palace|palm|panda|panel|panic|panther|paper|parade|parent|park|parrot|party|pass|patch|path|patient|patrol|pattern|pause|pave|payment|peace|peanut|pear|peasant|pelican|pen|penalty|pencil|people|pepper|perfect|permit|person|pet|phone|photo|phrase|physical|piano|picnic|picture|piece|pig|pigeon|pill|pilot|pink|pioneer|pipe|pistol|pitch|pizza|place|planet|plastic|plate|play|please|pledge|pluck|plug|plunge|poem|poet|point|polar|pole|police|pond|pony|pool|popular|portion|position|possible|post|potato|pottery|poverty|powder|power|practice|praise|predict|prefer|prepare|present|pretty|prevent|price|pride|primary|print|priority|prison|private|prize|problem|process|produce|profit|program|project|promote|proof|property|prosper|protect|proud|provide|public|pudding|pull|pulp|pulse|pumpkin|punch|pupil|puppy|purchase|purity|purpose|purse|push|put|puzzle|pyramid|quality|quantum|quarter|question|quick|quiet|quit|quiz|quote|rabbit|raccoon|race|rack|radar|radio|rail|rain|raise|rally|ranch|random|range|rapid|rare|rate|rather|raven|raw|razor|ready|real|reason|rebel|recall|receive|recipe|record|recycle|reduce|reflect|reform|refuse|region|regret|regular|reject|relax|release|relief|rely|remain|remember|remind|remove|render|renew|rent|repair|repeat|replace|report|require|rescue|resemble|resist|resource|response|result|retire|retreat|return|reunion|reveal|review|reward|rhythm|rib|ribbon|rice|rich|ride|ridge|rifle|right|rigid|ring|riot|ripple|risk|ritual|rival|river|road|roast|robot|robust|rocket|romance|roof|room|rose|rotate|rough|round|route|royal|rubber|rude|rug|rule|run|runway|rural|sad|saddle|sadness|safe|sail|salad|salmon|salon|salt|salute|same|sample|sand|satisfy|satoshi|sauce|sausage|save|say|scale|scan|scare|scatter|scene|scheme|school|science|scissors|scorpion|scout|scrap|screen|script|scrub|sea|search|season|seat|second|secret|section|security|seed|seek|segment|select|sell|seminar|senior|sense|sentence|series|service|session|settle|setup|seven|shadow|shaft|shallow|share|shark|sharp|sheep|sheet|shelf|shell|shelter|shield|shift|shine|ship|shiver|shock|shoe|shoot|shop|short|shoulder|shove|shrimp|shrug|shuffle|shy|sibling|sick|side|siege|sight|sign|silent|silk|silly|silver|similar|simple|since|sing|siren|sister|situate|six|size|skate|sketch|ski|skill|skin|skirt|skull|slab|slam|sleep|slice|slide|slight|slim|slogan|slot|slow|slush|small|smart|smile|smoke|smooth|snack|snake|snap|sniff|snow|soap|soccer|social|sock|soda|soft|solar|soldier|solid|solution|solve|someone|song|soon|sorry|sort|soul|sound|soup|source|south|space|spare|spatial|spawn|speak|spear|special|speed|spell|spend|sphere|spice|spider|spike|spin|spirit|split|spoil|sponsor|spoon|sport|spot|spray|spread|spring|spy|square|squeeze|squirrel|stable|stadium|staff|stage|stairs|stamp|stand|start|state|stay|steak|steel|stem|step|stereo|stick|still|sting|stock|stomach|stone|stool|story|stove|strategy|street|strike|strong|struggle|student|stuff|stumble|style|subject|submit|subway|success|such|sudden|suffer|sugar|suggest|suit|summer|sun|sunny|sunset|super|supply|supreme|sure|surface|surge|surprise|surround|survey|suspect|sustain|swallow|swamp|swap|swarm|swear|sweet|swift|swim|swing|switch|sword|symbol|symptom|syrup|system|table|tackle|tag|tail|talent|talk|tank|tape|target|task|taste|tattoo|taxi|teach|team|tell|ten|tenant|tennis|tent|term|test|text|thank|that|theme|then|theory|there|they|thing|this|thought|three|thrive|throw|thumb|thunder|ticket|tiger|tilt|timber|time|tiny|tip|tired|tissue|title|toast|tobacco|today|toddler|toe|together|toilet|token|tomato|tomorrow|tone|tongue|tonight|tool|tooth|top|topic|topple|torch|tornado|tortoise|toss|total|tourist|toward|tower|town|toy|track|trade|traffic|tragic|train|transfer|trap|trash|travel|treat|tree|trend|trial|tribe|trick|trigger|trim|trip|trophy|trouble|truck|true|trumpet|trust|truth|try|tube|tuition|tumble|tuna|tunnel|turkey|turn|turtle|twelve|twenty|twice|twin|twist|type|typical|ugly|umbrella|unable|unaware|uncle|uncover|under|undo|unfair|unfold|unhappy|uniform|unique|unit|universe|unknown|unlock|until|unusual|unveil|update|upgrade|uphold|upon|upper|upset|urban|urge|usage|use|used|useful|useless|usual|utility|vacant|vacuum|vague|valid|valley|valve|van|vanish|vapor|various|vast|vault|vehicle|velvet|vendor|venture|venue|verb|verify|version|very|vessel|veteran|viable|vibrant|vicious|victory|video|view|village|vintage|violin|virtual|virus|visa|visit|visual|vital|vivid|vocal|voice|volcano|volume|vote|voyage|wage|wagon|waist|wait|walk|wall|walnut|want|war|warm|warn|wash|wasp|waste|water|wave|way|wealth|weapon|wear|weasel|weather|web|wedding|weekend|weird|welcome|west|wet|whale|what|wheat|wheel|when|where|whip|whisper|wide|width|wife|wild|will|win|window|wine|wing|wink|winner|winter|wire|wisdom|wise|wish|witness|wolf|woman|wonder|wood|wool|word|work|world|worry|worth|wrap|wreck|wrestle|wrist|write|wrong|yard|year|yellow|you|young|youth|zebra|zero|zone|zoo){12}\b',
            'BIP39 12-word seed phrase',
            DataCategory.SEED_PHRASE,
            'CRITICAL'
        ),
        'bip39_seed_24': (
            r'\b(?:abandon|ability|able|about|above|absent|absorb|abstract|absurd|abuse|abuse|academy|accent|accept|access|accident|account|accuse|achieve|acid|acoustic|acquire|across|act|action|actor|actress|actual|adapt|add|addict|address|adjust|admit|adult|advance|advice|aerobic|affair|afford|afraid|again|age|agent|agree|ahead|aim|air|airport|aisle|alarm|album|alcohol|alert|alien|alike|alive|all|alley|allow|almost|alone|alpha|already|also|alter|always|amateur|amazing|among|amount|amused|analyst|anchor|ancient|anger|angle|angry|animal|ankle|announce|annual|another|answer|antenna|antique|anxiety|any|apart|apology|appear|apple|approve|april|arch|arctic|area|arena|argue|arm|armed|armor|army|around|arrange|arrest|arrive|arrow|art|artefact|artist|artwork|ask|aspect|assault|asset|assist|assume|asthma|athlete|atom|attack|attend|attitude|attract|auction|audit|august|aunt|author|auto|autumn|average|avocado|avoid|awake|aware|away|awesome|awful|awkward|axis|baby|bachelor|bacon|badge|bag|balance|balcony|ball|bamboo|banana|banner|bar|barely|bargain|barrel|base|basic|basket|battle|beach|bean|beauty|because|become|beef|before|begin|behave|behind|believe|below|belt|bench|benefit|best|betray|better|between|beyond|bicycle|bid|bike|bind|biology|bird|birth|bitter|black|blade|blame|blanket|blast|bleak|bless|blind|blood|blossom|blouse|blue|blur|blush|board|boat|body|boil|bomb|bone|bonus|book|boost|border|boring|borrow|boss|bottom|bounce|box|boy|bracket|brain|brake|branch|brand|brass|brave|bread|breeze|brick|bridge|brief|bright|brilliant|bring|broccoli|broken|bronze|broom|brother|brown|brush|bubble|buddy|budget|buffalo|build|bulb|bulk|bullet|bundle|bunker|burden|burger|burst|bus|business|busy|butter|buyer|buzz|cabbage|cabin|cable|cactus|cage|cake|call|calm|camera|camp|can|canal|cancel|candy|cannon|canoe|canvas|canyon|capable|capital|captain|car|carbon|card|cargo|carpet|carry|cart|case|cash|casino|castle|casual|cat|catalog|catch|category|cattle|caught|cause|caution|cave|ceiling|celery|cement|census|center|century|cereal|certain|chair|chalk|champion|change|chaos|chapter|charge|chase|chat|cheap|check|cheese|chef|cherry|chest|chicken|chief|child|chimney|choice|choose|chronic|chuckle|chunk|churn|cigar|cinnamon|circle|citizen|city|civil|claim|clap|clarify|claw|clay|clean|clerk|clever|click|client|cliff|climb|clinic|clip|clock|clog|close|cloth|cloud|clown|club|clump|cluster|clutch|coach|coast|coconut|code|coffee|coil|coin|collect|color|column|combine|come|comfort|comic|common|company|concert|conduct|confirm|congress|connect|consider|control|cook|cool|copper|copy|coral|core|corn|correct|cost|cotton|couch|country|couple|course|cousin|cover|coyote|crack|cradle|craft|cram|crane|crash|crater|crawl|crazy|cream|credit|creek|crew|cricket|crime|crisp|critic|crop|cross|crouch|crowd|crucial|cruel|cruise|crumble|crunch|crush|cry|crystal|cube|culture|cup|cupboard|curious|current|curtain|curve|cushion|custom|cute|cycle|dad|damage|damp|dance|danger|daring|dash|daughter|dawn|day|deal|debate|debris|decade|december|decide|decline|decorate|decrease|deer|defense|define|defy|degree|delay|deliver|demand|demise|denial|dentist|deny|depart|depend|deposit|depth|deputy|derive|describe|desert|design|desk|despair|destroy|detail|detect|develop|device|devote|diagram|dial|diamond|diary|dice|diesel|diet|differ|digital|dignity|dilemma|dinner|dinosaur|direct|dirt|disagree|discover|disease|dish|dismiss|disorder|display|distance|divert|divide|divorce|dizzy|doctor|document|dog|doll|dolphin|domain|donate|donkey|donor|door|dose|double|dove|draft|dragon|drama|drastic|draw|dream|dress|drift|drill|drink|drip|drive|drop|drum|dry|duck|dumb|dune|during|dust|dutch|duty|dwarf|dynamic|eager|eagle|early|earn|earth|easily|east|easy|echo|ecology|economy|edge|edit|educate|effort|egg|eight|either|elbow|elder|electric|elegant|element|elephant|elevator|elite|else|embark|embody|embrace|emerge|emotion|employ|empty|enable|enact|end|endless|endorse|enemy|energy|enforce|engage|engine|enhance|enjoy|enlist|enough|enrich|enroll|ensure|enter|entire|entry|envelope|episode|equal|equip|era|erase|erode|erosion|error|erupt|escape|essay|essence|estate|eternal|ethics|evidence|evil|evoke|evolve|exact|example|excess|exchange|excite|exclude|excuse|execute|exercise|exhaust|exhibit|exile|exist|exit|exotic|expand|expect|expire|explain|explode|explore|export|expose|express|extend|extra|eye|eyebrow|fabric|face|faculty|fade|faint|faith|fall|false|fame|family|famous|fan|fancy|fantasy|farm|fashion|fat|fatal|father|fatigue|fault|favorite|feature|february|federal|fee|feed|feel|female|fence|festival|fetch|fever|few|fiber|fiction|field|fierce|fiery|fifth|fight|figure|file|film|filter|final|find|fine|finger|finish|fire|firm|first|fiscal|fish|fit|fitness|fix|flag|flame|flash|flat|flavor|flee|flight|flip|float|flock|floor|flower|fluid|flush|flute|fly|foam|focus|fog|foil|fold|follow|food|foot|force|forest|forget|fork|fortune|forum|forward|fossil|foster|found|fox|fragile|frame|frequent|fresh|friend|fringe|frog|front|frost|frown|frozen|fruit|fuel|fun|funny|furnace|fury|future|gadget|gain|galaxy|gallery|game|gap|garage|garbage|garden|garlic|garment|gas|gasp|gate|gather|gauge|gaze|general|genius|genre|gentle|genuine|gesture|ghost|giant|gift|giggle|ginger|giraffe|girl|give|glad|glance|glare|glass|glide|glimpse|globe|gloom|glory|glove|glow|glue|goat|goddess|gold|good|goose|gospel|gossip|govern|gown|grab|grace|grade|gradual|grain|grand|grant|grape|grass|gravity|great|green|grid|grief|grit|grocery|group|grow|grunt|guard|guess|guide|guilt|guitar|gun|gym|habit|hair|half|hammer|hamster|hand|happy|harbor|hard|harsh|harvest|hat|have|hawk|hazard|head|health|heart|heavy|hedgehog|height|hello|help|hen|hero|hidden|high|hill|hint|hip|hire|history|hobby|hockey|hold|hole|holiday|hollow|holy|home|honey|hood|hope|horn|horror|horse|hospital|host|hotel|hour|hover|hub|huge|human|humble|humor|hundred|hungry|hunt|hurdle|hurry|hurt|husband|hybrid|ice|icon|idea|identify|idle|ignore|ill|illegal|illness|image|imitate|immense|immune|impact|impose|improve|impulse|inch|include|income|increase|index|indicate|indoor|industry|infant|inflict|inform|inhale|inject|injury|inmate|inner|innocent|input|inquiry|insane|insect|inside|inspire|install|intact|interest|into|invest|invite|involve|iron|island|isolate|issue|item|ivory|jacket|jaguar|jar|jazz|jealous|jeans|jelly|jewel|job|join|joke|journey|joy|judge|juice|jump|jungle|junior|junk|just|kangaroo|keen|keep|ketchup|key|kick|kid|kidney|kind|kingdom|kiss|kit|kitchen|kite|kitten|kiwi|knee|knife|knock|know|lab|label|labor|ladder|lady|lake|lamp|language|laptop|large|later|latin|laugh|laundry|lava|law|lawn|lawsuit|layer|lazy|leader|leaf|learn|leave|lecture|left|leg|legal|legend|leisure|lemon|lend|length|lens|leopard|lesson|letter|level|liar|liberty|library|license|life|lift|light|like|limb|limit|link|lion|liquid|list|little|live|lizard|load|loan|lobster|local|lock|logic|lonely|long|loop|lottery|loud|lounge|love|loyal|lucky|luggage|lumber|lunar|lunch|luxury|lyrics|machine|mad|magic|magnet|maid|mail|main|major|make|mammal|man|manage|mandate|mango|mansion|manual|maple|marble|march|margin|marine|market|marriage|mask|mass|master|match|material|math|matrix|matter|maximum|maze|meadow|mean|measure|meat|mechanic|medal|media|melody|melt|member|memory|mention|menu|mercy|merge|merit|merry|message|metal|method|middle|midnight|milk|million|mimic|mind|minimum|minister|minor|minute|miracle|mirror|misery|miss|mistake|mix|mixed|mixture|moan|model|modify|mom|moment|monitor|monkey|month|moon|moral|more|morning|mosquito|mother|motion|motor|mountain|mouse|move|movie|much|muffin|mule|multiply|muscle|museum|mushroom|music|must|mutual|myself|mystery|myth|naive|name|napkin|narrow|nasty|nation|nature|near|neck|need|negative|neglect|neither|nephew|nerve|nest|net|network|neutral|never|news|next|nice|niece|night|nine|noble|noise|nominee|noodle|normal|north|nose|notable|note|nothing|notice|novel|now|nuclear|number|nurse|nut|oak|obey|object|oblige|obscure|observe|obtain|obvious|occur|ocean|october|odor|off|offer|office|often|oil|okay|old|olive|olympic|omit|once|one|onion|online|only|open|opera|opinion|opponent|option|orange|orbit|orchard|order|ordinary|organ|orient|original|orphan|ostrich|other|outer|outfit|oval|oven|over|own|owner|oxygen|oyster|ozone|pact|paddle|page|pair|palace|palm|panda|panel|panic|panther|paper|parade|parent|park|parrot|party|pass|patch|path|patient|patrol|pattern|pause|pave|payment|peace|peanut|pear|peasant|pelican|pen|penalty|pencil|people|pepper|perfect|permit|person|pet|phone|photo|phrase|physical|piano|picnic|picture|piece|pig|pigeon|pill|pilot|pink|pioneer|pipe|pistol|pitch|pizza|place|planet|plastic|plate|play|please|pledge|pluck|plug|plunge|poem|poet|point|polar|pole|police|pond|pony|pool|popular|portion|position|possible|post|potato|pottery|poverty|powder|power|practice|praise|predict|prefer|prepare|present|pretty|prevent|price|pride|primary|print|priority|prison|private|prize|problem|process|produce|profit|program|project|promote|proof|property|prosper|protect|proud|provide|public|pudding|pull|pulp|pulse|pumpkin|punch|pupil|puppy|purchase|purity|purpose|purse|push|put|puzzle|pyramid|quality|quantum|quarter|question|quick|quiet|quit|quiz|quote|rabbit|raccoon|race|rack|radar|radio|rail|rain|raise|rally|ranch|random|range|rapid|rare|rate|rather|raven|raw|razor|ready|real|reason|rebel|recall|receive|recipe|record|recycle|reduce|reflect|reform|refuse|region|regret|regular|reject|relax|release|relief|rely|remain|remember|remind|remove|render|renew|rent|repair|repeat|replace|report|require|rescue|resemble|resist|resource|response|result|retire|retreat|return|reunion|reveal|review|reward|rhythm|rib|ribbon|rice|rich|ride|ridge|rifle|right|rigid|ring|riot|ripple|risk|ritual|rival|river|road|roast|robot|robust|rocket|romance|roof|room|rose|rotate|rough|round|route|royal|rubber|rude|rug|rule|run|runway|rural|sad|saddle|sadness|safe|sail|salad|salmon|salon|salt|salute|same|sample|sand|satisfy|satoshi|sauce|sausage|save|say|scale|scan|scare|scatter|scene|scheme|school|science|scissors|scorpion|scout|scrap|screen|script|scrub|sea|search|season|seat|second|secret|section|security|seed|seek|segment|select|sell|seminar|senior|sense|sentence|series|service|session|settle|setup|seven|shadow|shaft|shallow|share|shark|sharp|sheep|sheet|shelf|shell|shelter|shield|shift|shine|ship|shiver|shock|shoe|shoot|shop|short|shoulder|shove|shrimp|shrug|shuffle|shy|sibling|sick|side|siege|sight|sign|silent|silk|silly|silver|similar|simple|since|sing|siren|sister|situate|six|size|skate|sketch|ski|skill|skin|skirt|skull|slab|slam|sleep|slice|slide|slight|slim|slogan|slot|slow|slush|small|smart|smile|smoke|smooth|snack|snake|snap|sniff|snow|soap|soccer|social|sock|soda|soft|solar|soldier|solid|solution|solve|someone|song|soon|sorry|sort|soul|sound|soup|source|south|space|spare|spatial|spawn|speak|spear|special|speed|spell|spend|sphere|spice|spider|spike|spin|spirit|split|spoil|sponsor|spoon|sport|spot|spray|spread|spring|spy|square|squeeze|squirrel|stable|stadium|staff|stage|stairs|stamp|stand|start|state|stay|steak|steel|stem|step|stereo|stick|still|sting|stock|stomach|stone|stool|story|stove|strategy|street|strike|strong|struggle|student|stuff|stumble|style|subject|submit|subway|success|such|sudden|suffer|sugar|suggest|suit|summer|sun|sunny|sunset|super|supply|supreme|sure|surface|surge|surprise|surround|survey|suspect|sustain|swallow|swamp|swap|swarm|swear|sweet|swift|swim|swing|switch|sword|symbol|symptom|syrup|system|table|tackle|tag|tail|talent|talk|tank|tape|target|task|taste|tattoo|taxi|teach|team|tell|ten|tenant|tennis|tent|term|test|text|thank|that|theme|then|theory|there|they|thing|this|thought|three|thrive|throw|thumb|thunder|ticket|tiger|tilt|timber|time|tiny|tip|tired|tissue|title|toast|tobacco|today|toddler|toe|together|toilet|token|tomato|tomorrow|tone|tongue|tonight|tool|tooth|top|topic|topple|torch|tornado|tortoise|toss|total|tourist|toward|tower|town|toy|track|trade|traffic|tragic|train|transfer|trap|trash|travel|treat|tree|trend|trial|tribe|trick|trigger|trim|trip|trophy|trouble|truck|true|trumpet|trust|truth|try|tube|tuition|tumble|tuna|tunnel|turkey|turn|turtle|twelve|twenty|twice|twin|twist|type|typical|ugly|umbrella|unable|unaware|uncle|uncover|under|undo|unfair|unfold|unhappy|uniform|unique|unit|universe|unknown|unlock|until|unusual|unveil|update|upgrade|uphold|upon|upper|upset|urban|urge|usage|use|used|useful|useless|usual|utility|vacant|vacuum|vague|valid|valley|valve|van|vanish|vapor|various|vast|vault|vehicle|velvet|vendor|venture|venue|verb|verify|version|very|vessel|veteran|viable|vibrant|vicious|victory|video|view|village|vintage|violin|virtual|virus|visa|visit|visual|vital|vivid|vocal|voice|volcano|volume|vote|voyage|wage|wagon|waist|wait|walk|wall|walnut|want|war|warm|warn|wash|wasp|waste|water|wave|way|wealth|weapon|wear|weasel|weather|web|wedding|weekend|weird|welcome|west|wet|whale|what|wheat|wheel|when|where|whip|whisper|wide|width|wife|wild|will|win|window|wine|wing|wink|winner|winter|wire|wisdom|wise|wish|witness|wolf|woman|wonder|wood|wool|word|work|world|worry|worth|wrap|wreck|wrestle|wrist|write|wrong|yard|year|yellow|you|young|youth|zebra|zero|zone|zoo){24}\b',
            'BIP39 24-word seed phrase',
            DataCategory.SEED_PHRASE,
            'CRITICAL'
        ),
        'wif_private_key': (
            r'\b[5KL][1-9A-HJ-NP-Za-km-z]{50,51}\b',
            'Bitcoin WIF private key',
            DataCategory.BITCOIN_SECRET,
            'CRITICAL'
        ),
        'xpub_key': (
            r'\bxpub[a-zA-Z0-9]{107,108}\b',
            'Bitcoin xpub (extended public key)',
            DataCategory.BITCOIN_SECRET,
            'HIGH'
        ),
        'xprv_key': (
            r'\bxprv[a-zA-Z0-9]{107,108}\b',
            'Bitcoin xprv (extended private key)',
            DataCategory.BITCOIN_SECRET,
            'CRITICAL'
        ),
        'zpub_key': (
            r'\bzpub[a-zA-Z0-9]{107,108}\b',
            'Bitcoin zpub (SegWit public key)',
            DataCategory.BITCOIN_SECRET,
            'HIGH'
        ),
        'zprv_key': (
            r'\bzprv[a-zA-Z0-9]{107,108}\b',
            'Bitcoin zprv (SegWit private key)',
            DataCategory.BITCOIN_SECRET,
            'CRITICAL'
        ),
        'ypub_key': (
            r'\bypub[a-zA-Z0-9]{107,108}\b',
            'Bitcoin ypub (P2SH public key)',
            DataCategory.BITCOIN_SECRET,
            'HIGH'
        ),
        'yprv_key': (
            r'\byprv[a-zA-Z0-9]{107,108}\b',
            'Bitcoin yprv (P2SH private key)',
            DataCategory.BITCOIN_SECRET,
            'CRITICAL'
        ),
        'bitcoin_address_legacy': (
            r'\b1[a-km-zA-HJ-NP-Z1-9]{25,34}\b',
            'Bitcoin address (Legacy P2PKH)',
            DataCategory.BITCOIN_ADDRESS,
            'MEDIUM'
        ),
        'bitcoin_address_p2sh': (
            r'\b3[a-km-zA-HJ-NP-Z1-9]{25,34}\b',
            'Bitcoin address (P2SH)',
            DataCategory.BITCOIN_ADDRESS,
            'MEDIUM'
        ),
        'bitcoin_address_bech32': (
            r'\bbc1[a-z0-9]{6,87}\b',
            'Bitcoin address (Bech32 SegWit)',
            DataCategory.BITCOIN_ADDRESS,
            'MEDIUM'
        ),
        'bip38_encrypted': (
            r'\b6P[a-zA-Z0-9]{56,58}\b',
            'BIP38 encrypted private key',
            DataCategory.BITCOIN_SECRET,
            'CRITICAL'
        ),
    }
    
    # ========== NOSTR PATTERNS ==========
    NOSTR = {
        'nsec_key': (
            r'\bnsec1[a-zA-Z0-9]{58,}\b',
            'Nostr private key (nsec)',
            DataCategory.NOSTR_SECRET,
            'CRITICAL'
        ),
        'npub_key': (
            r'\bnpub1[a-zA-Z0-9]{58,}\b',
            'Nostr public key (npub)',
            DataCategory.NOSTR_PUBLIC,
            'MEDIUM'
        ),
        'nprofile': (
            r'\bnprofile1[a-zA-Z0-9]+\b',
            'Nostr profile (nprofile)',
            DataCategory.NOSTR_PUBLIC,
            'LOW'
        ),
        'nevent': (
            r'\bnevent1[a-zA-Z0-9]+\b',
            'Nostr event reference (nevent)',
            DataCategory.NOSTR_PUBLIC,
            'LOW'
        ),
        'naddr': (
            r'\bnaddr1[a-zA-Z0-9]+\b',
            'Nostr address (naddr)',
            DataCategory.NOSTR_PUBLIC,
            'LOW'
        ),
        'nostr_hex_private': (
            r'\b[0-9a-fA-F]{64}\b',
            'Nostr hex private key (64 hex chars)',
            DataCategory.NOSTR_SECRET,
            'CRITICAL'
        ),
    }
    
    # ========== LIGHTNING NETWORK PATTERNS ==========
    LIGHTNING = {
        'lightning_invoice': (
            r'\blnbc[a-zA-Z0-9]+\b',
            'Lightning Network invoice (bolt11)',
            DataCategory.LIGHTNING,
            'LOW'
        ),
        'lightning_node_pubkey': (
            r'\b[0-9a-fA-F]{66}\b',
            'Lightning node public key (33 bytes hex)',
            DataCategory.LIGHTNING,
            'MEDIUM'
        ),
        'lnd_macaroon': (
            r'\b[0-9a-fA-F]{160,640}\b',
            'LND macaroon (admin/invoice)',
            DataCategory.LIGHTNING,
            'CRITICAL'
        ),
        'lnd_tls_cert': (
            r'-----BEGIN CERTIFICATE-----[\s\S]*?-----END CERTIFICATE-----',
            'LND TLS certificate',
            DataCategory.LIGHTNING,
            'MEDIUM'
        ),
        'lightning_address': (
            r'\b[a-zA-Z0-9_.-]+@[a-zA-Z0-9.-]+\b',
            'Lightning address (user@domain)',
            DataCategory.LIGHTNING,
            'LOW'
        ),
    }
    
    # ========== FINANCIAL PATTERNS ==========
    FINANCIAL = {
        'credit_card': (
            r'\b(?:4[0-9]{12}(?:[0-9]{3})?|5[1-5][0-9]{14}|3[47][0-9]{13}|3(?:0[0-5]|[68][0-9])[0-9]{11}|6(?:011|5[0-9]{2})[0-9]{12}|(?:2131|1800|35\d3})\d11})\b',
            'Credit card number',
            DataCategory.CREDIT_CARD,
            'CRITICAL'
        ),
        'credit_card_spaced': (
            r'\b(?:\d4}[-\s]?){3}\d4}\b',
            'Credit card (spaced/hyphenated)',
            DataCategory.CREDIT_CARD,
            'CRITICAL'
        ),
        'iban': (
            r'\b[A-Z]{2}[0-9]{2}[A-Z0-9]{4}[0-9]{7}(?:[A-Z0-9]?){0,16}\b',
            'IBAN (International Bank Account)',
            DataCategory.FINANCIAL_ACCOUNT,
            'CRITICAL'
        ),
        'swift_bic': (
            r'\b[A-Z]{4}[A-Z]{2}[A-Z0-9]{2}(?:[A-Z0-9]{3})?\b',
            'SWIFT/BIC code',
            DataCategory.FINANCIAL_ACCOUNT,
            'MEDIUM'
        ),
        'us_bank_account': (
            r'\b\d8,17}\b',
            'US bank account number',
            DataCategory.FINANCIAL_ACCOUNT,
            'HIGH'
        ),
        'routing_number': (
            r'\b[0-9]{9}\b',
            'US routing number (9 digits)',
            DataCategory.FINANCIAL_ACCOUNT,
            'HIGH'
        ),
        'crypto_exchange_api': (
            r'\b[a-zA-Z0-9]{32,64}\b',
            'Cryptocurrency exchange API key',
            DataCategory.API_KEY,
            'CRITICAL'
        ),
    }
    
    # ========== PERSONAL DATA PATTERNS ==========
    PERSONAL = {
        'email': (
            r'\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Z|a-z]{2,}\b',
            'Email address',
            DataCategory.EMAIL,
            'MEDIUM'
        ),
        'phone_us': (
            r'\b(?:\+?1[-.\s]?)?\(?[0-9]{3}\)?[-.\s]?[0-9]{3}[-.\s]?[0-9]{4}\b',
            'US phone number',
            DataCategory.PHONE_NUMBER,
            'MEDIUM'
        ),
        'phone_international': (
            r'\b\+[0-9]{1,3}[-.\s]?[0-9]{1,14}\b',
            'International phone number',
            DataCategory.PHONE_NUMBER,
            'MEDIUM'
        ),
        'ssn': (
            r'\b\d3}[-\s]?\d2}[-\s]?\d4}\b',
            'Social Security Number (US)',
            DataCategory.SSN,
            'CRITICAL'
        ),
        'passport_us': (
            r'\b\d9}\b',
            'US passport number',
            DataCategory.PASSPORT,
            'CRITICAL'
        ),
        'passport_other': (
            r'\b[A-Z]{1,2}\d6,9}\b',
            'International passport number',
            DataCategory.PASSPORT,
            'CRITICAL'
        ),
        'gps_coordinates': (
            r'[-+]?([1-8]?\d(\.\d+)?|90(\.0+)?),\s*[-+]?(180(\.0+)?|((1[0-7]\d)|([1-9]?\d))(\.\d+)?)',
            'GPS coordinates (lat, long)',
            DataCategory.GPS_COORDINATES,
            'MEDIUM'
        ),
        'address_us': (
            r'\d+\s+[A-Za-z]+\s+(?:Street|St|Avenue|Ave|Road|Rd|Boulevard|Blvd|Drive|Dr|Lane|Ln|Court|Ct|Circle|Cir|Way|Place|Pl)\b',
            'US street address',
            DataCategory.PERSONAL_ADDRESS,
            'HIGH'
        ),
    }
    
    # ========== NETWORK PATTERNS ==========
    NETWORK = {
        'ipv4': (
            r'\b(?:(?:25[0-5]|2[0-4][0-9]|[01]?[0-9][0-9]?)\.)3}(?:25[0-5]|2[0-4][0-9]|[01]?[0-9][0-9]?)\b',
            'IPv4 address',
            DataCategory.IP_ADDRESS,
            'MEDIUM'
        ),
        'ipv6': (
            r'\b(?:[0-9a-fA-F]{1,4}:){7}[0-9a-fA-F]{1,4}\b',
            'IPv6 address (full)',
            DataCategory.IP_ADDRESS,
            'MEDIUM'
        ),
        'ipv6_compressed': (
            r'\b(?:[0-9a-fA-F]{1,4}:){1,7}:\b',
            'IPv6 address (compressed)',
            DataCategory.IP_ADDRESS,
            'MEDIUM'
        ),
        'mac_address': (
            r'\b(?:[0-9A-Fa-f]{2}[:-])5}(?:[0-9A-Fa-f]{2})\b',
            'MAC address',
            DataCategory.MAC_ADDRESS,
            'LOW'
        ),
        'wifi_ssid': (
            r'(?:ssid|network|wifi)[\s]*[=:][\s]*["\'][^"\']+["\']',
            'WiFi network name (SSID)',
            DataCategory.WIFI_CREDENTIAL,
            'MEDIUM'
        ),
        'wifi_password': (
            r'(?:password|psk|passphrase)[\s]*[=:][\s]*["\'][^"\']+["\']',
            'WiFi password',
            DataCategory.WIFI_CREDENTIAL,
            'HIGH'
        ),
        'wpa_psk': (
            r'\b[0-9a-fA-F]{64}\b',
            'WPA/WPA2 PSK (64 hex chars)',
            DataCategory.WIFI_CREDENTIAL,
            'CRITICAL'
        ),
    }
    
    # ========== SECRETS PATTERNS ==========
    SECRETS = {
        'generic_api_key': (
            r'\b(?:api[_-]?key|apikey)[\s]*[=:][\s]*["\']?[a-zA-Z0-9_-]{16,64}["\']?',
            'Generic API key',
            DataCategory.API_KEY,
            'CRITICAL'
        ),
        'bearer_token': (
            r'\bBearer\s+[a-zA-Z0-9_-]{20,}\b',
            'Bearer token',
            DataCategory.API_KEY,
            'CRITICAL'
        ),
        'private_key_pem': (
            r'-----BEGIN (?:RSA |DSA |EC |OPENSSH |PGP )?PRIVATE KEY-----[\s\S]*?-----END (?:RSA |DSA |EC |OPENSSH |PGP )?PRIVATE KEY-----',
            'PEM private key',
            DataCategory.PRIVATE_KEY,
            'CRITICAL'
        ),
        'ssh_private_key': (
            r'-----BEGIN OPENSSH PRIVATE KEY-----[\s\S]*?-----END OPENSSH PRIVATE KEY-----',
            'SSH private key',
            DataCategory.PRIVATE_KEY,
            'CRITICAL'
        ),
        'jwt_token': (
            r'\beyJ[a-zA-Z0-9_-]*\.eyJ[a-zA-Z0-9_-]*\.[a-zA-Z0-9_-]*\b',
            'JWT token',
            DataCategory.API_KEY,
            'HIGH'
        ),
        'aws_access_key': (
            r'\bAKIA[0-9A-Z]{16}\b',
            'AWS access key ID',
            DataCategory.API_KEY,
            'CRITICAL'
        ),
        'aws_secret_key': (
            r'\b[A-Za-z0-9/+=]{40}\b',
            'AWS secret key',
            DataCategory.API_KEY,
            'CRITICAL'
        ),
        'github_token': (
            r'\bgh[pousr]_[A-Za-z0-9_]{36,}\b',
            'GitHub personal access token',
            DataCategory.API_KEY,
            'CRITICAL'
        ),
        'slack_token': (
            r'\bxox[baprs]-[0-9]{10,13}-[0-9]{10,13}(-[a-zA-Z0-9]{24})?\b',
            'Slack token',
            DataCategory.API_KEY,
            'CRITICAL'
        ),
        'discord_token': (
            r'\b[A-Za-z0-9_-]{24}\.[A-Za-z0-9_-]{6}\.[A-Za-z0-9_-]{27}\b',
            'Discord bot token',
            DataCategory.API_KEY,
            'CRITICAL'
        ),
        'telegram_bot_token': (
            r'\b[0-9]{9,10}:[a-zA-Z0-9_-]{35}\b',
            'Telegram bot token',
            DataCategory.API_KEY,
            'CRITICAL'
        ),
        'stripe_key': (
            r'\bsk_live_[a-zA-Z0-9]{24,}\b',
            'Stripe live secret key',
            DataCategory.API_KEY,
            'CRITICAL'
        ),
        'stripe_test_key': (
            r'\bsk_test_[a-zA-Z0-9]{24,}\b',
            'Stripe test key (warning)',
            DataCategory.API_KEY,
            'MEDIUM'
        ),
    }
    
    # Combined all patterns
    ALL_PATTERNS = {
        **BITCOIN,
        **NOSTR,
        **LIGHTNING,
        **FINANCIAL,
        **PERSONAL,
        **NETWORK,
        **SECRETS
    }


class DLPScanner:
    """
    Data Loss Prevention Scanner
    
    Scans text for sensitive patterns and blocks/leaks if found.
    """
    
    def __init__(self):
        self.patterns = DLPPatterns.ALL_PATTERNS
        self._compile_patterns()
        self.violations: List[DLPViolation] = []
        
    def _compile_patterns(self):
        """Compile regex patterns for performance."""
        self.compiled_patterns = {}
        for name, (pattern, description, category, severity) in self.patterns.items():
            try:
                self.compiled_patterns[name] = {
                    'regex': re.compile(pattern, re.IGNORECASE),
                    'description': description,
                    'category': category,
                    'severity': severity
                }
            except re.error as e:
                logger.error(f"Failed to compile pattern {name}: {e}")
                
    def scan(self, text: str, context: str = "") -> Tuple[bool, List[DLPViolation]]:
        """
        Scan text for sensitive data patterns.
        
        Args:
            text: Text to scan
            context: Context for logging (e.g., "chat_response", "file_content")
            
        Returns:
            Tuple of (is_clean, violations)
        """
        violations = []
        
        for name, pattern_info in self.compiled_patterns.items():
            regex = pattern_info['regex']
            description = pattern_info['description']
            category = pattern_info['category']
            severity = pattern_info['severity']
            
            for match in regex.finditer(text):
                matched_text = match.group(0)
                
                # Create redacted version (show first/last 4 chars only)
                if len(matched_text) > 12:
                    redacted = f"matched_text[:4]}...matched_text[-4:]}"
                else:
                    redacted = "***"
                    
                violation = DLPViolation(
                    category=category,
                    pattern_name=name,
                    matched_text=matched_text,
                    position=(match.start(), match.end()),
                    severity=severity,
                    description=description,
                    redacted=redacted
                )
                violations.append(violation)
                
                # Log without exposing the actual data
                logger.warning(
                    f"DLP: {severity} violation detected: {description} "
                    f"in {context} (hash: {violation.text_hash})"
                )
        
        self.violations.extend(violations)
        is_clean = len(violations) == 0
        
        return is_clean, violations
    
    def sanitize(self, text: str) -> str:
        """
        Sanitize text by redacting sensitive patterns.
        
        Args:
            text: Original text
            
        Returns:
            Sanitized text with sensitive data redacted
        """
        sanitized = text
        
        # Scan for violations
        _, violations = self.scan(text, "sanitization")
        
        # Replace in reverse order to preserve positions
        for violation in sorted(violations, key=lambda v: v.position[0], reverse=True):
            start, end = violation.position
            sanitized = sanitized[:start] + f"[{violation.category.value.upper()}_REDACTED]" + sanitized[end:]
        
        return sanitized
    
    def get_summary(self) -> Dict:
        """Get summary of all detected violations."""
        if not self.violations:
            return {"clean": True, "violations": []}
        
        summary = {
            "clean": False,
            "total_violations": len(self.violations),
            "by_category": {},
            "by_severity": {"CRITICAL": 0, "HIGH": 0, "MEDIUM": 0, "LOW": 0},
            "violations": []
        }
        
        for v in self.violations:
            # Count by category
            cat = v.category.value
            summary["by_category"][cat] = summary["by_category"].get(cat, 0) + 1
            
            # Count by severity
            summary["by_severity"][v.severity] = summary["by_severity"].get(v.severity, 0) + 1
            
            # Add violation details (without actual data)
            summary["violations"].append({
                "category": v.category.value,
                "pattern": v.pattern_name,
                "severity": v.severity,
                "description": v.description,
                "redacted": v.redacted,
                "hash": v.text_hash
            })
        
        return summary
    
    def clear(self):
        """Clear all recorded violations."""
        self.violations = []


# Global scanner instance
dlp_scanner: Optional[DLPScanner] = None


def get_dlp_scanner() -> DLPScanner:
    """Get singleton DLP scanner instance."""
    global dlp_scanner
    if dlp_scanner is None:
        dlp_scanner = DLPScanner()
    return dlp_scanner


def scan_for_secrets(text: str, context: str = "") -> Tuple[bool, List[DLPViolation]]:
    """
    Convenience function to scan text for secrets.
    
    Args:
        text: Text to scan
        context: Context description
        
    Returns:
        (is_clean, violations)
    """
    scanner = get_dlp_scanner()
    return scanner.scan(text, context)


def sanitize_text(text: str) -> str:
    """Convenience function to sanitize text."""
    scanner = get_dlp_scanner()
    return scanner.sanitize(text)


# Forbidden code patterns (for code execution)
FORBIDDEN_CODE_PATTERNS = [
    # File system access
    r'\bopen\s*\(\s*(?:["\']|[\w./~])',
    r'\bfile\s*=\s*open',
    r'\.read\s*\(\s*\)',
    r'\.write\s*\(',
    r'os\.path\.',
    r'pathlib\.',
    r'shutil\.',
    r'glob\.',
    r'\.listdir\s*\(',
    r'\.walk\s*\(',
    r'__file__',
    r'\.env',
    
    # Network access
    r'import\s+socket',
    r'urllib\.',
    r'requests\.',
    r'httpx\.',
    r'aiohttp\.',
    r'\.connect\s*\(',
    r'socket\.',
    
    # System access
    r'os\.system',
    r'subprocess\.',
    r'platform\.',
    r'getpass\.',
    r'pwd\.',
    r'grp\.',
    r'sys\.argv',
    
    # Process manipulation
    r'exec\s*\(',
    r'eval\s*\(',
    r'compile\s*\(',
    r'__import__',
    r'\.load_module',
    r'importlib\.',
    
    # Crypto/Secrets handling
    r'base64\.',
    r'binascii\.',
    r'hashlib\.',
    r'secrets\.',
    r'key\s*=\s*',
    r'seed\s*=\s*',
    r'private\s*=\s*',
    r'secret\s*=\s*',
    
    # Self-modification
    r'__main__',
    r'sys\.modules',
    r'\.pyc',
    r'\.pyo',
    r'compileall\.',
    
    # Data exfiltration
    r'print\s*\([^)]*password',
    r'print\s*\([^)]*secret',
    r'print\s*\([^)]*key',
]


class CodeSecurityScanner:
    """Scan code for forbidden patterns before execution."""
    
    def __init__(self):
        self.forbidden_patterns = [
            re.compile(pattern, re.IGNORECASE) 
            for pattern in FORBIDDEN_CODE_PATTERNS
        ]
    
    def scan_code(self, code: str) -> Tuple[bool, List[str]]:
        """
        Scan code for forbidden patterns.
        
        Returns:
            (is_safe, violations)
        """
        violations = []
        
        for i, line in enumerate(code.split('\n'), 1):
            for pattern in self.forbidden_patterns:
                if pattern.search(line):
                    violations.append(f"Line {i}: Forbidden pattern '{pattern.pattern[:30]}...'")
                    break
        
        return len(violations) == 0, violations


# Global code scanner
code_security_scanner: Optional[CodeSecurityScanner] = None


def get_code_security_scanner() -> CodeSecurityScanner:
    """Get singleton code security scanner."""
    global code_security_scanner
    if code_security_scanner is None:
        code_security_scanner = CodeSecurityScanner()
    return code_security_scanner
