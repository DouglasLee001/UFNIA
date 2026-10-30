// One DAG pass over the caller's Z3 context. No parsing, tactics, or solving.
// This library must use precisely the libz3 loaded by the Python caller.
#include <z3.h>
#include <cstdint>
#include <exception>
#include <string>
#include <unordered_map>
#include <utility>
#include <vector>

namespace {
thread_local std::string last_error;
struct Failure {int code;std::string message;};
bool supported(Z3_decl_kind kind) {
    switch(kind) {
    case Z3_OP_TRUE:case Z3_OP_FALSE:case Z3_OP_UNINTERPRETED:case Z3_OP_ANUM:
    case Z3_OP_EQ:case Z3_OP_DISTINCT:case Z3_OP_ITE:case Z3_OP_AND:case Z3_OP_OR:
    case Z3_OP_NOT:case Z3_OP_IFF:case Z3_OP_XOR:case Z3_OP_IMPLIES:
    case Z3_OP_ADD:case Z3_OP_SUB:case Z3_OP_UMINUS:case Z3_OP_MUL:
    case Z3_OP_LE:case Z3_OP_GE:case Z3_OP_LT:case Z3_OP_GT:
    case Z3_OP_IDIV:case Z3_OP_MOD:case Z3_OP_REM:return true;
    default:return false;
    }
}
struct OwnedVector {
    Z3_context context;Z3_ast_vector vector;
    explicit OwnedVector(Z3_context c):context(c),vector(Z3_mk_ast_vector(c)){Z3_ast_vector_inc_ref(c,vector);}
    ~OwnedVector(){if(vector)Z3_ast_vector_dec_ref(context,vector);}
    void push(Z3_ast ast){Z3_ast_vector_push(context,vector,ast);}
    Z3_ast_vector release(){auto result=vector;vector=nullptr;return result;}
};
struct AstReferences {
    Z3_context context;std::vector<Z3_ast> values;
    explicit AstReferences(Z3_context c):context(c){}
    ~AstReferences(){for(auto it=values.rbegin();it!=values.rend();++it)Z3_dec_ref(context,*it);}
    Z3_ast retain(Z3_ast value){Z3_inc_ref(context,value);values.push_back(value);return value;}
};
bool normalized_supported(Z3_decl_kind kind){
    switch(kind){
    case Z3_OP_TRUE:case Z3_OP_FALSE:case Z3_OP_ANUM:case Z3_OP_UNINTERPRETED:
    case Z3_OP_NOT:case Z3_OP_AND:case Z3_OP_OR:case Z3_OP_IMPLIES:case Z3_OP_ITE:
    case Z3_OP_ADD:case Z3_OP_SUB:case Z3_OP_UMINUS:case Z3_OP_MUL:
    case Z3_OP_EQ:case Z3_OP_IFF:case Z3_OP_LE:case Z3_OP_GE:case Z3_OP_LT:case Z3_OP_GT:return true;
    default:return false;
    }
}
void analyze_normalized(Z3_context context,Z3_ast_vector assertions,OwnedVector& symbols,
                        size_t& atoms,size_t& nodes){
    atoms=0;nodes=0;
    std::vector<Z3_ast> pending;
    std::vector<uint64_t> seen;
    const unsigned count=Z3_ast_vector_size(context,assertions);
    for(unsigned i=0;i<count;++i){
        auto root=Z3_ast_vector_get(context,assertions,i);
        if(Z3_get_sort_kind(context,Z3_get_sort(context,root))!=Z3_BOOL_SORT)
            throw Failure{2,"assertions must have Bool sort"};
        pending.push_back(root);
    }
    while(!pending.empty()){
        const auto expression=pending.back();pending.pop_back();
        const unsigned id=Z3_get_ast_id(context,expression);
        const size_t word=id/64;const uint64_t bit=uint64_t(1)<<(id%64);
        if(word>=seen.size())seen.resize(word+1,0);
        if(seen[word]&bit)continue;
        seen[word]|=bit;++nodes;
        const auto ast_kind=Z3_get_ast_kind(context,expression);
        if(ast_kind!=Z3_APP_AST && ast_kind!=Z3_NUMERAL_AST)
            throw Failure{2,"quantifiers and bound variables are unsupported in normalized input"};
        const auto sort=Z3_get_sort_kind(context,Z3_get_sort(context,expression));
        if(sort!=Z3_INT_SORT && sort!=Z3_BOOL_SORT)
            throw Failure{2,"normalized input supports only Int and Bool sorts"};
        const auto app=Z3_to_app(context,expression);
        const auto declaration=Z3_get_app_decl(context,app);
        const auto kind=Z3_get_decl_kind(context,declaration);
        const unsigned arity=Z3_get_app_num_args(context,app);
        if(kind==Z3_OP_UNINTERPRETED){
            if(arity)throw Failure{2,"uninterpreted functions remain in normalized input"};
            symbols.push(expression);if(sort==Z3_BOOL_SORT)++atoms;
        }else{
            if(!normalized_supported(kind))throw Failure{2,"operator unsupported by the GMP core remains in normalized input: "+std::to_string(unsigned(kind))};
            if(sort==Z3_BOOL_SORT && (kind==Z3_OP_LE || kind==Z3_OP_GE || kind==Z3_OP_LT || kind==Z3_OP_GT ||
                (kind==Z3_OP_EQ && arity==2 && Z3_get_sort_kind(context,Z3_get_sort(context,Z3_get_app_arg(context,app,0)))==Z3_INT_SORT)))++atoms;
        }
        // This deterministic order also defines canonical symbol indices.
        for(unsigned i=0;i<arity;++i)pending.push_back(Z3_get_app_arg(context,app,i));
    }
}

}

extern "C" {
const char* ufnia_frontend_error(){return last_error.c_str();}
const char* ufnia_frontend_z3_version(){return Z3_get_full_version();}
uintptr_t ufnia_frontend_ast_kind_address(){return reinterpret_cast<uintptr_t>(&Z3_get_ast_kind);}
void ufnia_frontend_release(Z3_context context,Z3_ast_vector vector){if(vector)Z3_ast_vector_dec_ref(context,vector);}

// On success each output vector owns one reference, transferred to the caller.
// Application records keep the original declaration, applied to rewritten args.
int ufnia_frontend_prepare(Z3_context context,Z3_ast_vector assertions,
                          Z3_ast_vector* output_assertions,Z3_ast_vector* output_applications,
                          Z3_ast_vector* output_values,size_t* output_nodes) noexcept {
    *output_assertions=nullptr;*output_applications=nullptr;*output_values=nullptr;
    if(output_nodes)*output_nodes=0;
    last_error.clear();
    try {
        if(!context || !assertions)throw Failure{2,"missing Z3 context or assertion vector"};
        OwnedVector transformed(context),applications(context),values(context);
        AstReferences retained(context);
        std::unordered_map<Z3_ast,Z3_ast> rewritten;
        std::vector<std::pair<Z3_ast,bool>> pending;
        const unsigned count=Z3_ast_vector_size(context,assertions);
        for(unsigned i=0;i<count;++i){
            Z3_ast root=Z3_ast_vector_get(context,assertions,i);
            if(Z3_get_sort_kind(context,Z3_get_sort(context,root))!=Z3_BOOL_SORT)
                throw Failure{2,"assertions must have Bool sort"};
            pending.emplace_back(root,false);
        }
        std::vector<Z3_ast> arguments;
        while(!pending.empty()) {
            const auto entry=pending.back();pending.pop_back();Z3_ast expression=entry.first;
            if(rewritten.find(expression)!=rewritten.end())continue;
            const auto ast_kind=Z3_get_ast_kind(context,expression);
            if(ast_kind!=Z3_APP_AST && ast_kind!=Z3_NUMERAL_AST)
                throw Failure{2,"quantifiers and bound variables are unsupported"};
            const auto app=Z3_to_app(context,expression);
            const unsigned arity=Z3_get_app_num_args(context,app);
            const auto declaration=Z3_get_app_decl(context,app);
            const auto kind=Z3_get_decl_kind(context,declaration);
            if(!entry.second) {
                const auto sort_kind=Z3_get_sort_kind(context,Z3_get_sort(context,expression));
                if(sort_kind==Z3_UNINTERPRETED_SORT)
                    throw Failure{1,"uninterpreted carrier sort requires the Python carrier-encoding fallback"};
                if(sort_kind!=Z3_INT_SORT && sort_kind!=Z3_BOOL_SORT)
                    throw Failure{2,"unsupported sort; only Int and Bool are supported"};
                if(!supported(kind))throw Failure{2,"unsupported operator kind "+std::to_string(unsigned(kind))};
                pending.emplace_back(expression,true);
                // Match the reference pass's right-to-left DFS and record order.
                for(unsigned i=0;i<arity;++i)pending.emplace_back(Z3_get_app_arg(context,app,i),false);
                continue;
            }
            arguments.clear();arguments.reserve(arity);bool changed=false;
            for(unsigned i=0;i<arity;++i) {
                const auto original=Z3_get_app_arg(context,app,i);
                const auto replacement=rewritten.at(original);
                changed|=original!=replacement;arguments.push_back(replacement);
            }
            Z3_ast replacement=expression;
            if(kind==Z3_OP_UNINTERPRETED && arity) {
                // Retain immediately: later C API allocations can run RC GC.
                replacement=retained.retain(Z3_mk_fresh_const(context,"ufnia_app",Z3_get_sort(context,expression)));
                Z3_ast record=changed?Z3_mk_app(context,declaration,arity,arguments.data()):expression;
                applications.push(record);values.push(replacement);
            }else if(changed)replacement=retained.retain(Z3_update_term(context,expression,arity,arguments.data()));
            rewritten.emplace(expression,replacement);
        }
        for(unsigned i=0;i<count;++i)transformed.push(rewritten.at(Z3_ast_vector_get(context,assertions,i)));
        if(Z3_get_error_code(context)!=Z3_OK)throw Failure{3,"Z3 C API error during native abstraction"};
        if(output_nodes)*output_nodes=rewritten.size();
        *output_assertions=transformed.release();*output_applications=applications.release();*output_values=values.release();
        return 0;
    }catch(const Failure& error){last_error=error.message;return error.code;}
    catch(const std::exception& error){last_error=error.what();return 3;}
    catch(...){last_error="unknown native frontend failure";return 3;}
}

int ufnia_frontend_analyze(Z3_context context,Z3_ast_vector assertions,
                          Z3_ast_vector* output_symbols,size_t* output_atoms,size_t* output_nodes) noexcept {
    *output_symbols=nullptr;*output_atoms=0;*output_nodes=0;last_error.clear();
    try{
        OwnedVector symbols(context);
        analyze_normalized(context,assertions,symbols,*output_atoms,*output_nodes);
        *output_symbols=symbols.release();return 0;
    }catch(const Failure& error){last_error=error.message;return error.code;}
    catch(const std::exception& error){last_error=error.what();return 3;}
    catch(...){last_error="unknown normalized analysis failure";return 3;}
}

const char* ufnia_frontend_serialize(Z3_context context,Z3_ast_vector assertions,
                                   Z3_ast_vector supplied_symbols,int* output_code) noexcept {
    *output_code=0;last_error.clear();
    try{
        OwnedVector discovered(context);
        if(!supplied_symbols){size_t atoms=0,nodes=0;analyze_normalized(context,assertions,discovered,atoms,nodes);supplied_symbols=discovered.vector;}
        AstReferences retained(context);
        std::vector<Z3_ast> roots,from,to;
        const unsigned count=Z3_ast_vector_size(context,assertions);
        roots.reserve(count);
        for(unsigned i=0;i<count;++i)roots.push_back(Z3_ast_vector_get(context,assertions,i));
        auto conjunction=retained.retain(Z3_mk_and(context,count,roots.data()));
        const unsigned symbol_count=Z3_ast_vector_size(context,supplied_symbols);
        from.reserve(symbol_count);to.reserve(symbol_count);
        for(unsigned i=0;i<symbol_count;++i){
            const auto symbol=Z3_ast_vector_get(context,supplied_symbols,i);
            const auto sort=Z3_get_sort(context,symbol);
            const auto kind=Z3_get_sort_kind(context,sort);
            const auto app=Z3_to_app(context,symbol);
            if((kind!=Z3_INT_SORT && kind!=Z3_BOOL_SORT) || Z3_get_app_num_args(context,app)!=0 ||
                Z3_get_decl_kind(context,Z3_get_app_decl(context,app))!=Z3_OP_UNINTERPRETED)
                throw Failure{2,"canonical symbols must be zero-arity Int/Bool variables"};
            const std::string name=std::string(kind==Z3_BOOL_SORT?"b_":"i_")+std::to_string(i);
            const auto replacement=retained.retain(Z3_mk_const(context,Z3_mk_string_symbol(context,name.c_str()),sort));
            from.push_back(symbol);to.push_back(replacement);
        }
        if(symbol_count)conjunction=retained.retain(Z3_substitute(context,conjunction,symbol_count,from.data(),to.data()));
        // A single assertion lets the printer share DAG nodes across all clauses.
        const char* serialized=Z3_benchmark_to_smtlib_string(context,"ufnia normalized","QF_NIA","unknown","",0,nullptr,conjunction);
        if(Z3_get_error_code(context)!=Z3_OK)throw Failure{3,"Z3 C API normalized serialization failed"};
        return serialized;
    }catch(const Failure& error){last_error=error.message;*output_code=error.code;return nullptr;}
    catch(const std::exception& error){last_error=error.what();*output_code=3;return nullptr;}
    catch(...){last_error="unknown normalized serialization failure";*output_code=3;return nullptr;}
}

}
