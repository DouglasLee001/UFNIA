// Arbitrary-precision SLS entry point. Z3 supplies parsing and AST storage;
// all search, candidate selection, and SAT validation run in mpz_sls.h.
#include "mpz_sls.h"
#include <cstdlib>
#include <fstream>
#include <iomanip>
#include <iterator>
#include <limits>
#include <sstream>
#include <unordered_map>

namespace {
std::string json_string(const std::string& value) {
    std::ostringstream out;out << '"';
    for (unsigned char ch : value) {
        switch (ch) {
        case '"':out << "\\\"";break;
        case '\\':out << "\\\\";break;
        case '\n':out << "\\n";break;
        case '\r':out << "\\r";break;
        case '\t':out << "\\t";break;
        default:
            if (ch<32)out << "\\u" << std::hex << std::setw(4) << std::setfill('0') << unsigned(ch) << std::dec;
            else out << ch;
        }
    }
    out << '"';return out.str();
}
struct Unsupported : std::runtime_error {using std::runtime_error::runtime_error;};
struct Statistics {
    size_t assertions=0,ast_nodes=0,integer_variables=0,boolean_variables=0;
    size_t numerals=0,max_numeral_bits=0,multiplications=0,nonlinear_multiplications=0;
    std::string fields()const {
        std::ostringstream out;
        out << "\"assertions\":" << assertions << ",\"ast_nodes\":" << ast_nodes
            << ",\"integer_variables\":" << integer_variables << ",\"boolean_variables\":" << boolean_variables
            << ",\"numerals\":" << numerals << ",\"max_numeral_bits\":" << max_numeral_bits
            << ",\"multiplications\":" << multiplications
            << ",\"nonlinear_multiplications\":" << nonlinear_multiplications;
        return out.str();
    }
};
Statistics inspect(const z3::expr_vector& assertions) {
    Statistics stats;stats.assertions=assertions.size();
    std::unordered_map<unsigned,bool> symbolic;
    std::vector<std::pair<z3::expr,bool>> pending;
    for(unsigned i=0;i<assertions.size();++i)pending.emplace_back(assertions[i],false);
    while(!pending.empty()) {
        auto entry=pending.back();pending.pop_back();const auto& e=entry.first;
        if(symbolic.count(e.id()))continue;
        if(!e.is_app() || (!e.is_int() && !e.is_bool()))throw Unsupported("only quantifier-free Int and Bool expressions are supported");
        const auto kind=e.decl().decl_kind();
        if(!entry.second) {
            if(kind==Z3_OP_UNINTERPRETED) {
                if(!e.is_const())throw Unsupported("uninterpreted functions must be eliminated by the frontend");
                symbolic.emplace(e.id(),true);++stats.ast_nodes;
                if(e.is_int())++stats.integer_variables;else ++stats.boolean_variables;
                continue;
            }
            if(e.is_numeral()) {
                if(!e.is_int())throw Unsupported("non-integer numeral");
                mpz_class integer(Z3_get_numeral_string(e.ctx(),e));
                ++stats.numerals;++stats.ast_nodes;
                stats.max_numeral_bits=std::max(stats.max_numeral_bits,integer==0?size_t(0):mpz_sizeinbase(integer.get_mpz_t(),2));
                symbolic.emplace(e.id(),false);continue;
            }
            switch(kind) {
            case Z3_OP_TRUE:case Z3_OP_FALSE:case Z3_OP_NOT:case Z3_OP_AND:case Z3_OP_OR:
            case Z3_OP_IMPLIES:case Z3_OP_ITE:case Z3_OP_ADD:case Z3_OP_SUB:case Z3_OP_UMINUS:
            case Z3_OP_MUL:case Z3_OP_EQ:case Z3_OP_IFF:case Z3_OP_LE:case Z3_OP_GE:case Z3_OP_LT:case Z3_OP_GT:
                break;
            default:throw Unsupported("unsupported residual operator: "+e.decl().name().str());
            }
            pending.emplace_back(e,true);
            for(unsigned i=0;i<e.num_args();++i)pending.emplace_back(e.arg(i),false);
        } else {
            bool depends=false;unsigned factors=0;
            for(unsigned i=0;i<e.num_args();++i) {
                bool child=symbolic.at(e.arg(i).id());depends|=child;factors+=child;
            }
            if(kind==Z3_OP_MUL){++stats.multiplications;if(factors>=2)++stats.nonlinear_multiplications;}
            symbolic.emplace(e.id(),depends);++stats.ast_nodes;
        }
    }
    return stats;
}
void write_model(const std::string& path,z3::model model) {
    std::ofstream out(path);
    if(!out)throw std::runtime_error("cannot open model output: "+path);
    out << '{';
    for(unsigned i=0;i<model.num_consts();++i) {
        if(i)out << ',';
        auto decl=model.get_const_decl(i);auto value=model.get_const_interp(decl);
        out << '\n' << "  " << json_string(decl.name().str()) << ": ";
        if(value.is_bool())out << (value.is_true()?"true":"false");
        else out << json_string(Z3_get_numeral_string(model.ctx(),value));
    }
    out << "\n}\n";
    if(!out)throw std::runtime_error("cannot write model output: "+path);
}
void write_violations(const std::string& path,const ufnia::NiaSolver::Diagnostics& diagnostics) {
    std::ofstream out(path);if(!out)throw std::runtime_error("cannot open violations output: "+path);
    out << "{\"status\":\"unknown\",\"validated\":false,\"candidate_available\":true,"
        << "\"input_semantics\":\"core arithmetic input before preprocessing\","
        << "\"input_unsat_count\":" << diagnostics.input_unsat_count
        << ",\"working_unsat_count\":" << diagnostics.working_unsat_count
        << ",\"uf_conflicts\":" << diagnostics.uf_conflicts
        << ",\"limit\":8,\"truncated\":" << (diagnostics.truncated?"true":"false") << ",\"violations\":[";
    for(size_t i=0;i<diagnostics.violations.size();++i){
        if(i)out << ',';
        const auto& violation=diagnostics.violations[i];
        out << "{\"scope\":" << json_string(violation.scope) << ",\"expression\":" << json_string(violation.expression.to_string())
            << ",\"assignments\":{";
        for(size_t j=0;j<violation.assignments.size();++j){
            if(j)out << ',';
            const auto& assignment=violation.assignments[j];
            out << json_string(assignment.first.decl().name().str()) << ':';
            if(assignment.second.is_bool())out << (assignment.second.is_true()?"true":"false");
            else out << json_string(Z3_get_numeral_string(assignment.second.ctx(),assignment.second));
        }
        out << "}}";
    }
    out << "]}\n";if(!out)throw std::runtime_error("cannot write violations output: "+path);
}
struct Options {bool parse_only=false,uf_observers=false,active_arithmetic_rows=false;double seconds=1;unsigned seed=0;std::string input,model_output,candidate_output,violations_output,interaction="joint",scoring="count",distance="classic",arithmetic_repair="off",search_policy="legacy",search_phase="mixed",candidate_policy="broad";};
Options options(int argc,char** argv) {
    Options result;
    for(int i=1;i<argc;++i) {
        const std::string option=argv[i];
        auto value=[&](){if(i+1>=argc)throw std::runtime_error("missing value for "+option);return std::string(argv[++i]);};
        if(option=="--parse-only")result.parse_only=true;
        else if(option=="--uf-observers")result.uf_observers=true;
        else if(option=="--candidate-policy"){result.candidate_policy=value();if(result.candidate_policy!="broad" && result.candidate_policy!="critical")throw std::runtime_error("candidate-policy must be broad or critical");}
        else if(option=="--search-phase"){result.search_phase=value();if(result.search_phase!="mixed" && result.search_phase!="alternating")throw std::runtime_error("search-phase must be mixed or alternating");}
        else if(option=="--search-policy"){result.search_policy=value();if(result.search_policy!="legacy" && result.search_policy!="greedy")throw std::runtime_error("search-policy must be legacy or greedy");}
        else if(option=="--active-arithmetic-rows"){auto mode=value();if(mode!="off" && mode!="on")throw std::runtime_error("active-arithmetic-rows must be off or on");result.active_arithmetic_rows=mode=="on";}
        else if(option=="--arithmetic-repair"){result.arithmetic_repair=value();if(result.arithmetic_repair!="off" && result.arithmetic_repair!="linear" && result.arithmetic_repair!="quotient" && result.arithmetic_repair!="joint")throw std::runtime_error("arithmetic-repair must be off, linear, quotient, or joint");}
        else if(option=="--distance"){result.distance=value();if(result.distance!="classic" && result.distance!="structural")throw std::runtime_error("distance must be classic or structural");}
        else if(option=="--scoring"){result.scoring=value();if(result.scoring!="count" && result.scoring!="paws")throw std::runtime_error("scoring must be count or paws");}
        else if(option=="--interaction"){result.interaction=value();if(result.interaction!="none" && result.interaction!="soft" && result.interaction!="joint")throw std::runtime_error("interaction must be none, soft, or joint");}
        else if(option=="--seconds") {
            auto number=value();size_t used=0;result.seconds=std::stod(number,&used);
            if(used!=number.size() || !std::isfinite(result.seconds) || result.seconds<0 || result.seconds>4294967)
                throw std::runtime_error("seconds must be a finite number between 0 and 4294967");
        }else if(option=="--seed") {
            auto number=value();size_t used=0;auto parsed=std::stoull(number,&used);
            if(used!=number.size() || parsed>std::numeric_limits<unsigned>::max() || number[0]=='-')throw std::runtime_error("seed is outside unsigned range");
            result.seed=unsigned(parsed);
        }else if(option=="--model-output")result.model_output=value();
        else if(option=="--candidate-output")result.candidate_output=value();
        else if(option=="--violations-output")result.violations_output=value();
        else if(option=="--help") {
            std::cout << "usage: ufnia_core [SMT2_FILE|-] [--parse-only] [--seconds N] [--seed N] [--model-output FILE] [--candidate-output FILE] [--violations-output FILE] [--uf-observers] [--interaction none|soft|joint] [--scoring count|paws] [--distance classic|structural] [--arithmetic-repair off|linear|quotient|joint] [--active-arithmetic-rows off|on] [--search-policy legacy|greedy] [--search-phase mixed|alternating] [--candidate-policy broad|critical]\n";
            std::exit(0);
        }else if(option=="-" || option.empty() || option[0]!='-') {
            if(!result.input.empty())throw std::runtime_error("only one SMT-LIB input is accepted");
            result.input=option;
        }else throw std::runtime_error("unknown option: "+option);
    }
    if(result.search_phase=="alternating" && result.search_policy!="greedy")throw std::runtime_error("alternating search-phase requires greedy search-policy");
    if((!result.candidate_output.empty() && result.candidate_output==result.model_output) ||
       (!result.violations_output.empty() && (result.violations_output==result.model_output || result.violations_output==result.candidate_output)))
        throw std::runtime_error("validated model, candidate, and violations outputs must use different paths");
    return result;
}
}

int main(int argc,char** argv) {
    Options opt;
    try {opt=options(argc,argv);}catch(const std::exception& error) {
        std::cerr << "{\"status\":\"error\",\"error\":" << json_string(error.what()) << "}\n";return 2;
    }
    const auto started=std::chrono::steady_clock::now();
    try {
        z3::context context;z3::expr_vector assertions(context);
        if(opt.input.empty() || opt.input=="-") {
            std::ostringstream input;input << std::cin.rdbuf();assertions=context.parse_string(input.str().c_str());
        }else assertions=context.parse_file(opt.input.c_str());
        z3::expr_vector arithmetic(context),observed(context),inspection(context);
        std::vector<z3::expr> pending;
        for(unsigned i=assertions.size();i>0;--i)pending.push_back(assertions[i-1]);
        while(!pending.empty()){
            auto e=pending.back();pending.pop_back();
            if(e.is_and()){
                for(unsigned i=e.num_args();i>0;--i)pending.push_back(e.arg(i-1));
                continue;
            }
            bool marker=e.is_app() && e.decl().decl_kind()==Z3_OP_UNINTERPRETED &&
                e.decl().name().str().rfind("__ufnia_observe_",0)==0;
            if(opt.uf_observers && marker){
                if(!e.is_bool() || e.num_args()<2)throw Unsupported("invalid UF observation marker");
                observed.push_back(e);
                for(unsigned i=0;i<e.num_args();++i){
                    auto value=e.arg(i);
                    inspection.push_back(value.is_bool()?value:(value==0));
                }
            }else {arithmetic.push_back(e);inspection.push_back(e);}
        }
        const auto stats=inspect(inspection);
        if(opt.parse_only) {
            std::cout << "{\"status\":\"parsed\"," << stats.fields() << "}\n";return 0;
        }
        ufnia::sls_seed=opt.seed;ufnia::NiaSolver solver(context);
        solver.set("timeout",unsigned(std::ceil(opt.seconds*1000)));
        for(unsigned i=0;i<arithmetic.size();++i)solver.add(arithmetic[i]);
        solver.set_interaction(opt.interaction);solver.set_scoring(opt.scoring);solver.set_distance(opt.distance);solver.set_arithmetic_repair(opt.arithmetic_repair);solver.set_active_arithmetic_rows(opt.active_arithmetic_rows);solver.set_search_policy(opt.search_policy);solver.set_search_phase(opt.search_phase);solver.set_candidate_policy(opt.candidate_policy);
        for(unsigned i=0;i<observed.size();++i){
            const auto& e=observed[i];std::vector<z3::expr> arguments;
            for(unsigned j=0;j+1<e.num_args();++j)arguments.push_back(e.arg(j));
            solver.add_uf_application(e.decl().id(),arguments,e.arg(e.num_args()-1));
        }
        auto result=solver.check();
        const bool sat=result==z3::sat && solver.validated();
        if(sat && !opt.model_output.empty())write_model(opt.model_output,solver.get_model());
        bool candidate_written=false,violations_written=false;
        if(!sat && solver.candidate_available()){
            if(!opt.candidate_output.empty()){write_model(opt.candidate_output,solver.get_model());candidate_written=true;}
            if(!opt.violations_output.empty()){write_violations(opt.violations_output,solver.candidate_diagnostics());violations_written=true;}
        }
        std::cout << (sat?"sat":"unknown") << '\n';
        const double elapsed=std::chrono::duration<double>(std::chrono::steady_clock::now()-started).count();
        std::cerr << "{\"status\":\"" << (sat?"sat":"unknown") << "\",\"steps\":" << solver.steps_taken()
                  << ",\"validated\":" << (solver.validated()?"true":"false")
                  << ",\"candidate_available\":" << (solver.candidate_available()?"true":"false")
                  << ",\"candidate_written\":" << (candidate_written?"true":"false")
                  << ",\"violations_written\":" << (violations_written?"true":"false")
                  << ",\"uf_applications\":" << observed.size()
                  << ",\"uf_joint_moves\":" << solver.uf_joint_moves()
                  << ",\"uf_conflicts\":" << solver.uf_conflicts()
                  << ",\"weight_updates\":" << solver.weight_updates()
                  << ",\"candidate_policy\":" << json_string(opt.candidate_policy)
                  << ",\"candidate_probes\":" << solver.candidate_probes()
                  << ",\"critical_candidates\":" << solver.critical_candidates()
                  << ",\"fallback_candidates\":" << solver.fallback_candidates()
                  << ",\"search_phase\":" << json_string(opt.search_phase)
                  << ",\"bool_phase_steps\":" << solver.bool_phase_steps()
                  << ",\"int_phase_steps\":" << solver.int_phase_steps()
                  << ",\"phase_switches\":" << solver.phase_switches()
                  << ",\"boolean_joint_moves\":" << solver.boolean_joint_moves()
                  << ",\"phase_cross_escapes\":" << solver.phase_cross_escapes()
                  << ",\"search_policy\":" << json_string(opt.search_policy)
                  << ",\"greedy_rounds\":" << solver.greedy_rounds()
                  << ",\"greedy_evaluations\":" << solver.greedy_evaluations()
                  << ",\"random_escape_moves\":" << solver.random_escape_moves()
                  << ",\"paws_reselections\":" << solver.paws_reselections()
                  << ",\"distance\":" << json_string(opt.distance)
                  << ",\"arithmetic_repair\":" << json_string(opt.arithmetic_repair)
                  << ",\"arith_active_rows\":" << solver.arith_active_rows()
                  << ",\"arith_joint_moves\":" << solver.arith_joint_moves()
                  << ",\"arith_repair_proposals\":" << solver.arith_repair_proposals()
                  << ",\"arith_repair_attempts\":" << solver.arith_repair_attempts()
                  << ",\"arith_linear_rows\":" << solver.arith_linear_rows()
                  << ",\"arith_quotient_relations\":" << solver.arith_quotient_relations()
                  << ",\"initial_arithmetic_unsat\":" << (solver.candidate_available()?std::to_string(solver.initial_arithmetic_unsat()):"null")
                  << ",\"best_arithmetic_unsat\":" << (solver.candidate_available()?std::to_string(solver.best_arithmetic_unsat()):"null")
                  << ",\"higher_degree_fallbacks\":" << solver.higher_degree_fallbacks()
                  << ",\"seconds\":" << elapsed << ',' << stats.fields() << "}\n";
        return 0;
    }catch(const Unsupported& error) {
        if(opt.parse_only)std::cout << "{\"status\":\"unsupported\",\"error\":" << json_string(error.what()) << "}\n";
        else {std::cout << "unknown\n";std::cerr << "{\"status\":\"unsupported\",\"steps\":0,\"validated\":false,\"error\":" << json_string(error.what()) << "}\n";}
        return opt.parse_only?2:0;
    }catch(const z3::exception& error) {
        if(opt.parse_only)std::cout << "{\"status\":\"error\",\"error\":" << json_string(error.msg()) << "}\n";
        else {std::cout << "unknown\n";std::cerr << "{\"status\":\"error\",\"steps\":0,\"validated\":false,\"error\":" << json_string(error.msg()) << "}\n";}
        return opt.parse_only?2:0;
    }catch(const std::exception& error) {
        if(opt.parse_only)std::cout << "{\"status\":\"error\",\"error\":" << json_string(error.what()) << "}\n";
        else {std::cout << "unknown\n";std::cerr << "{\"status\":\"error\",\"steps\":0,\"validated\":false,\"error\":" << json_string(error.what()) << "}\n";}
        return opt.parse_only?2:0;
    }
}
