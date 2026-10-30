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
struct Options {bool parse_only=false;double seconds=1;unsigned seed=0;std::string input,model_output;};
Options options(int argc,char** argv) {
    Options result;
    for(int i=1;i<argc;++i) {
        const std::string option=argv[i];
        auto value=[&](){if(i+1>=argc)throw std::runtime_error("missing value for "+option);return std::string(argv[++i]);};
        if(option=="--parse-only")result.parse_only=true;
        else if(option=="--seconds") {
            auto number=value();size_t used=0;result.seconds=std::stod(number,&used);
            if(used!=number.size() || !std::isfinite(result.seconds) || result.seconds<0 || result.seconds>4294967)
                throw std::runtime_error("seconds must be a finite number between 0 and 4294967");
        }else if(option=="--seed") {
            auto number=value();size_t used=0;auto parsed=std::stoull(number,&used);
            if(used!=number.size() || parsed>std::numeric_limits<unsigned>::max() || number[0]=='-')throw std::runtime_error("seed is outside unsigned range");
            result.seed=unsigned(parsed);
        }else if(option=="--model-output")result.model_output=value();
        else if(option=="--help") {
            std::cout << "usage: ufnia_core [SMT2_FILE|-] [--parse-only] [--seconds N] [--seed N] [--model-output FILE]\n";
            std::exit(0);
        }else if(option=="-" || option.empty() || option[0]!='-') {
            if(!result.input.empty())throw std::runtime_error("only one SMT-LIB input is accepted");
            result.input=option;
        }else throw std::runtime_error("unknown option: "+option);
    }
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
        const auto stats=inspect(assertions);
        if(opt.parse_only) {
            std::cout << "{\"status\":\"parsed\"," << stats.fields() << "}\n";return 0;
        }
        ufnia::sls_seed=opt.seed;ufnia::NiaSolver solver(context);
        solver.set("timeout",unsigned(std::ceil(opt.seconds*1000)));
        for(unsigned i=0;i<assertions.size();++i)solver.add(assertions[i]);
        auto result=solver.check();
        const bool sat=result==z3::sat && solver.validated();
        if(sat && !opt.model_output.empty())write_model(opt.model_output,solver.get_model());
        std::cout << (sat?"sat":"unknown") << '\n';
        const double elapsed=std::chrono::duration<double>(std::chrono::steady_clock::now()-started).count();
        std::cerr << "{\"status\":\"" << (sat?"sat":"unknown") << "\",\"steps\":" << solver.steps_taken()
                  << ",\"validated\":" << (solver.validated()?"true":"false")
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
