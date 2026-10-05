import java.io.BufferedReader;
import java.io.FileReader;
import java.util.ArrayList;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import java.util.concurrent.CompletableFuture;
import java.util.concurrent.TimeUnit;

import com.google.common.collect.Lists;

import forge.ai.LobbyPlayerAi;
import forge.GuiDesktop;
import forge.StaticData;
import forge.deck.Deck;
import forge.game.Game;
import forge.game.GameRules;
import forge.game.GameStage;
import forge.game.GameState;
import forge.game.GameType;
import forge.game.Match;
import forge.game.ability.AbilityUtils;
import forge.game.card.Card;
import forge.game.card.CounterEnumType;
import forge.game.phase.PhaseType;
import forge.game.player.Player;
import forge.game.player.RegisteredPlayer;
import forge.game.spellability.SpellAbility;
import forge.game.zone.ZoneType;
import forge.gui.GuiBase;
import forge.localinstance.properties.ForgePreferences.FPref;
import forge.model.FModel;
import forge.util.ThreadUtil;

/**
 * Forge as an oracle (plan economia-de-operadores, Fase 3).
 *
 * Reads experiments from a file:
 *
 *   ### <id>
 *   <GameState lines: p0... is the opponent, p1... the acting player>
 *   @action <card name>|<zone: hand|battlefield>
 *   (blank line)
 *
 * For each one: build a fresh 2-player game, apply the state, resolve the card's
 * first spell (hand) or first non-mana activated ability (battlefield) as an
 * EFFECT ONLY — costs are not paid, the operator's cost is compared separately —
 * run state-based actions and print the resource vector before and after:
 *
 *   <id>\tOK\t<before k=v,...>\t<after k=v,...>
 *   <id>\tERR\t<message>
 *
 * Targets: the opponent if the ability can target it, else an opponent creature,
 * else one of the actor's creatures. Choices during resolution go to Forge's AI.
 */
public class ForgeOracle {

    public static void main(String[] args) throws Exception {
        GuiBase.setInterface(new GuiDesktop());
        FModel.initialize(null, prefs -> {
            prefs.setPref(FPref.LOAD_CARD_SCRIPTS_LAZILY, true);
            prefs.setPref(FPref.UI_LANGUAGE, "en-US");
            return null;
        });
        for (Experiment exp : read(args[0])) {
            try {
                // Forge mutates game state only on its game thread (GameAction.invoke
                // is asynchronous from anywhere else), so the whole experiment runs there.
                CompletableFuture<String> result = new CompletableFuture<>();
                ThreadUtil.invokeInGameThread(() -> {
                    try {
                        result.complete(run(exp));
                    } catch (Throwable t) {
                        result.completeExceptionally(t);
                    }
                });
                System.out.println(result.get(60, TimeUnit.SECONDS));
            } catch (Throwable t) {
                if (t instanceof java.util.concurrent.ExecutionException && t.getCause() != null) t = t.getCause();
                String msg = String.valueOf(t).replace('\t', ' ').replace('\n', ' ');
                System.out.println(exp.id + "\tERR\t" + msg);
            }
            System.out.flush();
        }
        System.exit(0);
    }

    static final class Experiment {
        String id;
        List<String> state = new ArrayList<>();
        String card;
        String zone = "hand";
    }

    static List<Experiment> read(String path) throws Exception {
        List<Experiment> out = new ArrayList<>();
        Experiment cur = null;
        try (BufferedReader br = new BufferedReader(new FileReader(path))) {
            String line;
            while ((line = br.readLine()) != null) {
                if (line.startsWith("### ")) {
                    cur = new Experiment();
                    cur.id = line.substring(4).trim();
                    out.add(cur);
                } else if (cur != null && line.startsWith("@action ")) {
                    String[] parts = line.substring(8).split("\\|");
                    cur.card = parts[0].trim();
                    if (parts.length > 1) cur.zone = parts[1].trim();
                } else if (cur != null && !line.isBlank()) {
                    cur.state.add(line);
                }
            }
        }
        return out;
    }

    static Game newGame() {
        List<RegisteredPlayer> players = Lists.newArrayList();
        Deck d = new Deck();
        players.add(new RegisteredPlayer(d).setPlayer(new LobbyPlayerAi("p0", null)));
        players.add(new RegisteredPlayer(d).setPlayer(new LobbyPlayerAi("p1", null)));
        GameRules rules = new GameRules(GameType.Constructed);
        Match match = new Match(rules, players, "Oracle");
        Game game = new Game(players, rules, match);
        game.setAge(GameStage.Play);
        game.getPhaseHandler().devModeSet(PhaseType.MAIN1, game.getPlayers().get(1));
        game.getPhaseHandler().onStackResolved();
        return game;
    }

    static String run(Experiment exp) {
        preload(exp);
        Game game = newGame();
        GameState gs = new GameState();
        List<String> lines = new ArrayList<>(exp.state);
        lines.add("activeplayer=p1");
        lines.add("activephase=MAIN1");
        gs.parse(lines);
        gs.applyToGame(game);
        Player opp = game.getPlayers().get(0);
        Player actor = game.getPlayers().get(1);

        ZoneType zone = "battlefield".equals(exp.zone) ? ZoneType.Battlefield : ZoneType.Hand;
        Card card = null;
        for (Card c : actor.getCardsIn(zone)) {
            if (c.getName().equals(exp.card)) { card = c; break; }
        }
        if (card == null) throw new IllegalStateException("card not found in " + zone + ": " + exp.card);

        SpellAbility sa = null;
        for (SpellAbility s : card.getSpellAbilities()) {
            if (zone == ZoneType.Hand ? s.isSpell() : (s.isActivatedAbility() && !s.isManaAbility())) { sa = s; break; }
        }
        if (sa == null && zone == ZoneType.Battlefield) {
            for (SpellAbility s : card.getManaAbilities()) { sa = s; break; }
        }
        if (sa == null) throw new IllegalStateException("no ability on " + exp.card);

        Map<String, Integer> before = vector(game);
        for (SpellAbility s = sa; s != null; s = s.getSubAbility()) {
            s.setActivatingPlayer(actor);
            if (s.usesTargeting()) chooseTarget(s, opp, actor);
        }
        AbilityUtils.resolve(sa);
        game.getAction().checkStateEffects(true);
        Map<String, Integer> after = vector(game);
        return exp.id + "\tOK\t" + fmt(before) + "\t" + fmt(after);
    }

    /** Lazy card loading: make sure every card named in the state is in the DB. */
    static void preload(Experiment exp) {
        List<String> names = new ArrayList<>();
        names.add(exp.card);
        for (String line : exp.state) {
            int eq = line.indexOf('=');
            String key = eq > 0 ? line.substring(0, eq).toLowerCase() : "";
            if (eq < 0 || !(key.endsWith("hand") || key.endsWith("battlefield") || key.endsWith("library")
                    || key.endsWith("graveyard") || key.endsWith("exile"))) continue;
            for (String item : line.substring(eq + 1).split(";")) {
                String name = item.split("\\|")[0].trim();
                if (!name.isEmpty()) names.add(name);
            }
        }
        for (String name : names) {
            if (StaticData.instance().getCommonCards().getCard(name) == null) {
                StaticData.instance().attemptToLoadCard(name);
            }
        }
    }

    static void chooseTarget(SpellAbility sa, Player opp, Player actor) {
        sa.resetTargets();
        if (sa.canTarget(opp)) { sa.getTargets().add(opp); return; }
        for (Card c : opp.getCardsIn(ZoneType.Battlefield)) {
            if (sa.canTarget(c)) { sa.getTargets().add(c); return; }
        }
        for (Card c : actor.getCardsIn(ZoneType.Battlefield)) {
            if (sa.canTarget(c)) { sa.getTargets().add(c); return; }
        }
        if (sa.canTarget(actor)) { sa.getTargets().add(actor); return; }
        for (Card c : opp.getCardsIn(ZoneType.Graveyard)) {
            if (sa.canTarget(c)) { sa.getTargets().add(c); return; }
        }
        for (Card c : actor.getCardsIn(ZoneType.Graveyard)) {
            if (sa.canTarget(c)) { sa.getTargets().add(c); return; }
        }
    }

    static Map<String, Integer> vector(Game game) {
        Map<String, Integer> v = new LinkedHashMap<>();
        List<Player> ps = game.getPlayers();
        for (int i = 0; i < ps.size(); i++) {
            Player p = ps.get(i);
            String k = "p" + i + ".";
            v.put(k + "life", p.getLife());
            v.put(k + "poison", p.getPoisonCounters());
            v.put(k + "energy", p.getCounters(CounterEnumType.ENERGY));
            v.put(k + "hand", p.getCardsIn(ZoneType.Hand).size());
            v.put(k + "library", p.getCardsIn(ZoneType.Library).size());
            v.put(k + "graveyard", p.getCardsIn(ZoneType.Graveyard).size());
            v.put(k + "exile", p.getCardsIn(ZoneType.Exile).size());
            v.put(k + "mana", p.getManaPool().totalMana());
            int creatures = 0, power = 0, toughness = 0, tokens = 0, artifacts = 0,
                enchantments = 0, lands = 0, planeswalkers = 0, p1p1 = 0, tapped = 0, permanents = 0;
            for (Card c : p.getCardsIn(ZoneType.Battlefield)) {
                permanents++;
                if (c.isCreature()) { creatures++; power += c.getNetPower(); toughness += c.getNetToughness(); }
                if (c.isToken()) tokens++;
                if (c.isArtifact()) artifacts++;
                if (c.isEnchantment()) enchantments++;
                if (c.isLand()) lands++;
                if (c.isPlaneswalker()) planeswalkers++;
                if (c.isTapped()) tapped++;
                p1p1 += c.getCounters(CounterEnumType.P1P1);
            }
            v.put(k + "permanents", permanents);
            v.put(k + "creatures", creatures);
            v.put(k + "power", power);
            v.put(k + "toughness", toughness);
            v.put(k + "tokens", tokens);
            v.put(k + "artifacts", artifacts);
            v.put(k + "enchantments", enchantments);
            v.put(k + "lands", lands);
            v.put(k + "planeswalkers", planeswalkers);
            v.put(k + "tapped", tapped);
            v.put(k + "p1p1", p1p1);
        }
        return v;
    }

    static String fmt(Map<String, Integer> v) {
        StringBuilder sb = new StringBuilder();
        for (Map.Entry<String, Integer> e : v.entrySet()) {
            if (sb.length() > 0) sb.append(',');
            sb.append(e.getKey()).append('=').append(e.getValue());
        }
        return sb.toString();
    }
}
